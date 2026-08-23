import os
import threading
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

ADMIN_ID = 7003609983

REQUIRED_CHANNEL = "@TaskMint_v1"
CHANNEL_LINK = "https://t.me/TaskMint_v1"

TOKEN_NAME = "POL"
DEFAULT_MIN_WITHDRAW = 1.0
DEFAULT_REF_COMMISSION = 0.5
DATABASE_NAME = "taskmint"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is missing.")


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server started on port {port}")
    server.serve_forever()


# =========================================================
# MONGODB COLLECTIONS & SETTINGS
# =========================================================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]
submissions_collection = db["task_submissions"]
settings_collection = db["settings"]


def setup_database():
    users_collection.create_index("user_id", unique=True)
    tasks_collection.create_index("task_id", unique=True)
    withdrawals_collection.create_index("user_id")
    withdrawals_collection.create_index("status")
    submissions_collection.create_index("user_id")
    submissions_collection.create_index("task_id")
    settings_collection.create_index("key", unique=True)

    print("MongoDB database ready.")


def get_setting(key, default_value):
    setting = settings_collection.find_one({"key": key})

    if setting:
        return setting.get("value", default_value)

    return default_value


def update_setting(key, value):
    settings_collection.update_one(
        {"key": key},
        {"$set": {"value": value}},
        upsert=True
    )


# =========================================================
# USER FUNCTIONS
# =========================================================

def create_or_update_user(user, referrer_id=None):

    now = datetime.now(timezone.utc)

    existing_user = users_collection.find_one(
        {"user_id": user.id}
    )

    if not existing_user:

        ref_by = None

        if referrer_id and referrer_id != user.id:

            ref_user = users_collection.find_one(
                {"user_id": referrer_id}
            )

            if ref_user:

                ref_by = referrer_id

                users_collection.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"referrals": 1}}
                )

        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                },
                "$setOnInsert": {
                    "user_id": user.id,
                    "balance": 0.0,
                    "referrals": 0,
                    "referred_by": ref_by,
                    "ref_bonus_paid": False,
                    "completed_tasks": [],
                    "is_banned": False,
                    "created_at": now
                }
            },
            upsert=True
        )

    else:

        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                }
            }
        )


def get_user(user_id):
    return users_collection.find_one(
        {"user_id": user_id}
    )


def get_balance(user_id):

    user = get_user(user_id)

    if not user:
        return 0.0

    return float(
        user.get("balance", 0.0)
    )


# =========================================================
# REFERRAL
# =========================================================

async def check_and_pay_referral(
    user_id,
    context
):

    user = get_user(user_id)

    if not user:
        return

    if user.get(
        "ref_bonus_paid",
        False
    ):
        return

    referred_by = user.get(
        "referred_by"
    )

    completed_tasks = user.get(
        "completed_tasks",
        []
    )

    if (
        referred_by
        and len(completed_tasks) >= 4
    ):

        ref_commission = float(
            get_setting(
                "ref_commission",
                DEFAULT_REF_COMMISSION
            )
        )

        result = users_collection.update_one(
            {
                "user_id": user_id,
                "ref_bonus_paid": False,
                "referred_by": {
                    "$ne": None
                }
            },
            {
                "$set": {
                    "ref_bonus_paid": True
                }
            }
        )

        if result.modified_count != 1:
            return

        users_collection.update_one(
            {"user_id": referred_by},
            {"$inc": {"balance": ref_commission}}
        )

        try:

            await context.bot.send_message(
                referred_by,
                (
                    "🎉 <b>Referral Bonus Unlocked!</b>\n\n"
                    "Your referred user has completed 4 tasks.\n"
                    f"💰 You received <b>+{ref_commission} "
                    f"{TOKEN_NAME}</b> commission!"
                ),
                parse_mode="HTML"
            )

        except Exception:
            pass


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(user_id):

    buttons = [
        ["🎯 Tasks"],
        ["💰 Balance", "💳 Withdraw"],
        ["👥 Refer"],
    ]

    if user_id == ADMIN_ID:
        buttons.append(
            ["👑 Admin Panel"]
        )

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True
    )


def join_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Done",
                callback_data="check_join"
            )
        ]
    ])


# =========================================================
# CHANNEL CHECK
# =========================================================

async def is_channel_member(
    bot,
    user_id
):

    try:

        member = await bot.get_chat_member(
            REQUIRED_CHANNEL,
            user_id
        )

        return member.status in [
            "member",
            "administrator",
            "creator"
        ]

    except Exception as error:

        print(
            "Channel check error:",
            error
        )

        return False


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referrer_id = None

    if context.args:

        try:
            referrer_id = int(
                context.args[0]
            )
        except ValueError:
            pass

    create_or_update_user(
        user,
        referrer_id
    )

    db_user = get_user(user.id)

    if (
        db_user
        and db_user.get(
            "is_banned",
            False
        )
    ):

        await update.message.reply_text(
            "❌ You are banned from using this bot."
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "Checking...",
        reply_markup=ReplyKeyboardRemove()
    )

    member = await is_channel_member(
        context.bot,
        user.id
    )

    if not member:

        await update.message.reply_text(
            (
                "👋 <b>Hi dear user!</b>\n\n"
                "Please join our official channel "
                "then the bot will become active for you.\n\n"
                "After joining, press <b>Done</b>."
            ),
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )

        return

    await send_main_menu(
        context.bot,
        user.id
    )


async def send_main_menu(
    bot,
    user_id
):

    await bot.send_message(
        chat_id=user_id,
        text=(
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


async def check_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    user_id = query.from_user.id

    member = await is_channel_member(
        context.bot,
        user_id
    )

    if not member:

        await query.answer(
            "❌ Please join the channel first.",
            show_alert=True
        )

        return

    await query.answer(
        "✅ Verified!"
    )

    await query.edit_message_text(
        "✅ <b>Done!</b>\n\n"
        "Your membership has been verified.",
        parse_mode="HTML"
    )

    await send_main_menu(
        context.bot,
        user_id
    )


# =========================================================
# BALANCE
# =========================================================

async def balance_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    balance = get_balance(user_id)

    await update.message.reply_text(
        (
            "💰 <b>Your Balance</b>\n\n"
            f"💎 Balance: <b>{balance:.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        parse_mode="HTML"
    )


# =========================================================
# TASK LIST
# =========================================================

async def tasks_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    tasks = list(
        tasks_collection.find(
            {"active": True}
        ).sort(
            "created_at",
            -1
        )
    )

    if not tasks:

        await update.message.reply_text(
            "🎯 <b>Tasks</b>\n\n"
            "No tasks are available right now.",
            parse_mode="HTML"
        )

        return

    buttons = []

    for task in tasks:

        total_slots = int(
            task.get(
                "total_slots",
                0
            ) or 0
        )

        completed_slots = int(
            task.get(
                "completed_slots",
                0
            ) or 0
        )

        remaining = (
            total_slots
            - completed_slots
        )

        if (
            total_slots > 0
            and remaining <= 0
        ):

            tasks_collection.update_one(
                {
                    "task_id": task["task_id"]
                },
                {
                    "$set": {
                        "active": False
                    }
                }
            )

            continue

        buttons.append([
            InlineKeyboardButton(
                f"🎯 {escape(str(task.get('title', 'Task')))}",
                callback_data=(
                    f"task_{task['task_id']}"
                )
            )
        ])

    if not buttons:

        await update.message.reply_text(
            "🎯 <b>Tasks</b>\n\n"
            "No tasks are available right now.",
            parse_mode="HTML"
        )

        return

    await update.message.reply_text(
        "🎯 <b>Available Tasks</b>",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML"
    )


# =========================================================
# TASK DETAILS
# =========================================================

async def task_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    task_id = query.data.replace(
        "task_",
        "",
        1
    )

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:

        await query.answer(
            "❌ Task not found or inactive.",
            show_alert=True
        )

        return

    keyboard = []

    link = task.get("link")

    if link:

        keyboard.append([
            InlineKeyboardButton(
                "🔗 Open Task Link",
                url=link
            )
        ])

    task_type = task.get(
        "task_type",
        "auto"
    )

    if task_type == "auto":

        keyboard.append([
            InlineKeyboardButton(
                "✅ Auto Verify & Complete",
                callback_data=(
                    f"complete_{task_id}"
                )
            )
        ])

    else:

        keyboard.append([
            InlineKeyboardButton(
                "📤 Submit Proof (Manual)",
                callback_data=(
                    f"submitproof_{task_id}"
                )
            )
        ])

    await query.answer()

    await query.edit_message_text(
        (
            f"🎯 <b>{escape(str(task.get('title', 'Task')))}</b>\n\n"
            f"📝 {escape(str(task.get('description', '')))}\n\n"
            f"🔹 Type: <b>{escape(str(task_type).upper())}</b>\n"
            f"💰 Reward: <b>{float(task.get('reward', 0)):.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML"
    )
# =========================================================
# AUTO TASK COMPLETION
# =========================================================

async def complete_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user_id = query.from_user.id

    task_id = query.data.replace(
        "complete_",
        "",
        1
    )

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:

        await query.answer(
            "❌ Task is no longer available.",
            show_alert=True
        )

        return

    task_link = str(
        task.get(
            "link",
            ""
        ) or ""
    ).strip()

    if "t.me/" in task_link:

        try:

            parsed = urlparse(
                task_link
            )

            channel_path = (
                parsed.path
                .strip("/")
                .split("/")[0]
            )

            channel_username = (
                "@"
                + channel_path
                if channel_path
                else ""
            )

            if not channel_username:

                raise ValueError(
                    "Invalid Telegram channel link"
                )

            member = await context.bot.get_chat_member(
                channel_username,
                user_id
            )

            if member.status not in [
                "member",
                "administrator",
                "creator"
            ]:

                await query.answer(
                    "❌ You have not joined the target channel yet! Join first.",
                    show_alert=True
                )

                return

        except Exception:

            await query.answer(
                "❌ Could not verify this Telegram task. Please contact admin.",
                show_alert=True
            )

            return

    already_done = users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    )

    if already_done:

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    reward = float(
        task.get(
            "reward",
            0
        )
    )

    total_slots = int(
        task.get(
            "total_slots",
            0
        ) or 0
    )

    slot_filter = {
        "task_id": task_id,
        "active": True
    }

    if total_slots > 0:

        slot_filter["$expr"] = {
            "$lt": [
                {
                    "$ifNull": [
                        "$completed_slots",
                        0
                    ]
                },
                "$total_slots"
            ]
        }

    slot_result = tasks_collection.update_one(
        slot_filter,
        {
            "$inc": {
                "completed_slots": 1
            }
        }
    )

    if slot_result.modified_count != 1:

        tasks_collection.update_one(
            {
                "task_id": task_id,
                "active": True,
                "total_slots": {
                    "$gt": 0
                }
            },
            {
                "$set": {
                    "active": False
                }
            }
        )

        await query.answer(
            "❌ Task slots are finished.",
            show_alert=True
        )

        return

    user_result = users_collection.update_one(
        {
            "user_id": user_id,
            "completed_tasks": {
                "$ne": task_id
            }
        },
        {
            "$inc": {
                "balance": reward
            },
            "$addToSet": {
                "completed_tasks": task_id
            }
        }
    )

    if user_result.modified_count != 1:

        tasks_collection.update_one(
            {
                "task_id": task_id
            },
            {
                "$inc": {
                    "completed_slots": -1
                }
            }
        )

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    if total_slots > 0:

        tasks_collection.update_one(
            {
                "task_id": task_id,
                "completed_slots": {
                    "$gte": total_slots
                }
            },
            {
                "$set": {
                    "active": False
                }
            }
        )

    await check_and_pay_referral(
        user_id,
        context
    )

    await query.answer(
        "✅ Task completed successfully!",
        show_alert=True
    )

    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward: <b>+{reward:.6f} "
            f"{TOKEN_NAME}</b>\n\n"
            "The reward has been added to your balance."
        ),
        parse_mode="HTML"
    )


# =========================================================
# MANUAL TASK PROOF REQUEST
# =========================================================

async def request_proof_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user_id = query.from_user.id

    task_id = query.data.replace(
        "submitproof_",
        "",
        1
    )

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:

        await query.answer(
            "❌ Task not available.",
            show_alert=True
        )

        return

    already_done = users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    )

    if already_done:

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    total_slots = int(
        task.get(
            "total_slots",
            0
        ) or 0
    )

    completed_slots = int(
        task.get(
            "completed_slots",
            0
        ) or 0
    )

    if (
        total_slots > 0
        and completed_slots >= total_slots
    ):

        tasks_collection.update_one(
            {
                "task_id": task_id
            },
            {
                "$set": {
                    "active": False
                }
            }
        )

        await query.answer(
            "❌ Task slots are finished.",
            show_alert=True
        )

        return

    pending_sub = submissions_collection.find_one(
        {
            "user_id": user_id,
            "task_id": task_id,
            "status": "pending"
        }
    )

    if pending_sub:

        await query.answer(
            "❌ You already have a pending submission for this task.",
            show_alert=True
        )

        return

    context.user_data[
        "submitting_task_id"
    ] = task_id

    await query.answer()

    await query.edit_message_text(
        (
            "📤 <b>Submit Manual Task Proof</b>\n\n"
            f"Task: <b>{escape(str(task.get('title', 'Task')))}</b>\n\n"
            "Please send your proof.\n"
            "You can send text, username, link or screenshot."
        ),
        parse_mode="HTML"
    )


# =========================================================
# REFER MENU
# =========================================================

async def refer_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    user = get_user(
        user_id
    )

    referrals = (
        user.get(
            "referrals",
            0
        )
        if user
        else 0
    )

    ref_commission = float(
        get_setting(
            "ref_commission",
            DEFAULT_REF_COMMISSION
        )
    )

    bot_info = await context.bot.get_me()

    referral_link = (
        f"https://t.me/"
        f"{bot_info.username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{referrals}</b>\n"
            f"🎁 Commission: <b>{ref_commission} "
            f"{TOKEN_NAME}</b> "
            "(After referred user completes 4 tasks)\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{escape(referral_link)}</code>"
        ),
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW
# =========================================================

async def withdraw_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    balance = get_balance(
        user_id
    )

    min_withdraw = float(
        get_setting(
            "min_withdraw",
            DEFAULT_MIN_WITHDRAW
        )
    )

    context.user_data.clear()

    if balance < min_withdraw:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: <b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\n"
                "❌ Insufficient balance."
            ),
            parse_mode="HTML"
        )

        return

    context.user_data[
        "withdraw_step"
    ] = "amount"

    await update.message.reply_text(
        (
            "💳 <b>POL Withdrawal</b>\n\n"
            f"💰 Available: <b>{balance:.6f} POL</b>\n"
            f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\n"
            "Enter withdrawal amount:"
        ),
        parse_mode="HTML"
    )


async def process_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    step = context.user_data.get(
        "withdraw_step"
    )

    min_withdraw = float(
        get_setting(
            "min_withdraw",
            DEFAULT_MIN_WITHDRAW
        )
    )

    if step == "amount":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Enter a valid amount."
            )

            return

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount must be greater than 0."
            )

            return

        balance = get_balance(
            user_id
        )

        if amount < min_withdraw:

            await update.message.reply_text(
                f"❌ Minimum is {min_withdraw} POL."
            )

            return

        if amount > balance:

            await update.message.reply_text(
                "❌ Insufficient balance."
            )

            return

        context.user_data[
            "withdraw_amount"
        ] = amount

        context.user_data[
            "withdraw_step"
        ] = "wallet"

        await update.message.reply_text(
            (
                "👛 <b>POL Wallet</b>\n\n"
                "Send your POL wallet address."
            ),
            parse_mode="HTML"
        )

        return

    if step == "wallet":

        wallet = text

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if not amount:

            context.user_data.clear()

            return

        if len(wallet) < 20:

            await update.message.reply_text(
                "❌ Please send a valid wallet address."
            )

            return

        result = users_collection.update_one(
            {
                "user_id": user_id,
                "balance": {
                    "$gte": amount
                }
            },
            {
                "$inc": {
                    "balance": -amount
                }
            }
        )

        if result.modified_count != 1:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Insufficient balance."
            )

            return

        now = datetime.now(
            timezone.utc
        )

        withdrawal = {
            "user_id": user_id,
            "amount": amount,
            "token": TOKEN_NAME,
            "wallet": wallet,
            "status": "pending",
            "created_at": now,
            "updated_at": now
        }

        try:

            result = withdrawals_collection.insert_one(
                withdrawal
            )

        except PyMongoError:

            users_collection.update_one(
                {
                    "user_id": user_id
                },
                {
                    "$inc": {
                        "balance": amount
                    }
                }
            )

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Withdrawal failed."
            )

            return

        wid = str(
            result.inserted_id
        )

        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Withdrawal Submitted</b>\n\n"
                f"🆔 ID: <code>{wid}</code>\n"
                f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                f"👛 Wallet: <code>{escape(wallet)}</code>\n"
                "📌 Status: <b>Pending</b>"
            ),
            parse_mode="HTML"
            )
# =========================================================
# ADMIN PANEL
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "🎯 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            ),
            InlineKeyboardButton(
                "📥 Submissions",
                callback_data="admin_submissions"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "💰 Balance Management",
                callback_data="admin_balance"
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ Bot Settings",
                callback_data="admin_settings"
            ),
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ]
    ])


async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        (
            "👑 <b>Admin Panel</b>\n\n"
            "Select an option:"
        ),
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    if data == "admin_home":

        await query.edit_message_text(
            "👑 <b>Admin Panel</b>\n\n"
            "Select an option:",
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # STATISTICS
    # =====================================================

    if data == "admin_stats":

        total_users = users_collection.count_documents({})
        total_tasks = tasks_collection.count_documents({})
        pending_w = withdrawals_collection.count_documents(
            {"status": "pending"}
        )
        pending_s = submissions_collection.count_documents(
            {"status": "pending"}
        )

        await query.edit_message_text(
            (
                "📊 <b>Statistics</b>\n\n"
                f"👥 Total Users: <b>{total_users}</b>\n"
                f"🎯 Total Tasks: <b>{total_tasks}</b>\n"
                f"🕐 Pending Withdrawals: <b>{pending_w}</b>\n"
                f"📥 Pending Submissions: <b>{pending_s}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # TASK MANAGER
    # =====================================================

    if data == "admin_tasks":

        active = tasks_collection.count_documents(
            {"active": True}
        )

        inactive = tasks_collection.count_documents(
            {"active": False}
        )

        await query.edit_message_text(
            (
                "🎯 <b>Manage Tasks & Slots</b>\n\n"
                f"🟢 Active: <b>{active}</b>\n"
                f"🔴 Inactive: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Add Task (with Slots)",
                        callback_data="task_add"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📋 View/Delete Tasks",
                        callback_data="task_list_admin"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    if data == "task_add":

        context.user_data[
            "admin_action"
        ] = "task_add_step1"

        await query.edit_message_text(
            "➕ <b>Add New Task</b>\n\n"
            "Send task title:",
            parse_mode="HTML"
        )

        return

    if data == "task_list_admin":

        tasks = list(
            tasks_collection.find({})
        )

        if not tasks:

            await query.edit_message_text(
                "No tasks found.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="admin_tasks"
                        )
                    ]
                ])
            )

            return

        buttons = []

        for t in tasks:

            total_slots = int(
                t.get(
                    "total_slots",
                    0
                ) or 0
            )

            completed_slots = int(
                t.get(
                    "completed_slots",
                    0
                ) or 0
            )

            rem = (
                total_slots
                - completed_slots
            )

            title = escape(
                str(
                    t.get(
                        "title",
                        "Task"
                    )
                )
            )

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"[{'ON' if t.get('active', False) else 'OFF'}] "
                        f"{title} "
                        f"(Rem: {rem})"
                    ),
                    callback_data=(
                        f"admin_deltask_{t['task_id']}"
                    )
                ),
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=(
                        f"admin_rmtask_{t['task_id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_tasks"
            )
        ])

        await query.edit_message_text(
            "🎯 <b>Manage Tasks</b>",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # TASK ON/OFF
    # =====================================================

    if data.startswith(
        "admin_deltask_"
    ):

        tid = data.replace(
            "admin_deltask_",
            "",
            1
        )

        t = tasks_collection.find_one(
            {
                "task_id": tid
            }
        )

        if t:

            new_status = not t.get(
                "active",
                True
            )

            tasks_collection.update_one(
                {
                    "task_id": tid
                },
                {
                    "$set": {
                        "active": new_status
                    }
                }
            )

        tasks = list(
            tasks_collection.find({})
        )

        buttons = []

        for t in tasks:

            total_slots = int(
                t.get(
                    "total_slots",
                    0
                ) or 0
            )

            completed_slots = int(
                t.get(
                    "completed_slots",
                    0
                ) or 0
            )

            rem = (
                total_slots
                - completed_slots
            )

            title = escape(
                str(
                    t.get(
                        "title",
                        "Task"
                    )
                )
            )

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"[{'ON' if t.get('active', False) else 'OFF'}] "
                        f"{title} (Rem: {rem})"
                    ),
                    callback_data=(
                        f"admin_deltask_{t['task_id']}"
                    )
                ),
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=(
                        f"admin_rmtask_{t['task_id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_tasks"
            )
        ])

        await query.edit_message_text(
            "Task status updated.",
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    # =====================================================
    # DELETE TASK
    # =====================================================

    if data.startswith(
        "admin_rmtask_"
    ):

        tid = data.replace(
            "admin_rmtask_",
            "",
            1
        )

        tasks_collection.delete_one(
            {
                "task_id": tid
            }
        )

        users_collection.update_many(
            {
                "completed_tasks": tid
            },
            {
                "$pull": {
                    "completed_tasks": tid
                }
            }
        )

        submissions_collection.delete_many(
            {
                "task_id": tid
            }
        )

        await query.answer(
            "🗑 Task deleted permanently!",
            show_alert=True
        )

        tasks = list(
            tasks_collection.find({})
        )

        buttons = []

        for t in tasks:

            total_slots = int(
                t.get(
                    "total_slots",
                    0
                ) or 0
            )

            completed_slots = int(
                t.get(
                    "completed_slots",
                    0
                ) or 0
            )

            rem = (
                total_slots
                - completed_slots
            )

            title = escape(
                str(
                    t.get(
                        "title",
                        "Task"
                    )
                )
            )

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"[{'ON' if t.get('active', False) else 'OFF'}] "
                        f"{title} (Rem: {rem})"
                    ),
                    callback_data=(
                        f"admin_deltask_{t['task_id']}"
                    )
                ),
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=(
                        f"admin_rmtask_{t['task_id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_tasks"
            )
        ])

        await query.edit_message_text(
            "Task deleted successfully.",
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    # =====================================================
    # SUBMISSIONS
    # =====================================================

    if data == "admin_submissions":

        pending_subs = list(
            submissions_collection.find(
                {
                    "status": "pending"
                }
            ).sort(
                "created_at",
                -1
            ).limit(10)
        )

        if not pending_subs:

            await query.edit_message_text(
                (
                    "📥 <b>Pending Submissions</b>\n\n"
                    "No pending submissions."
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="admin_home"
                        )
                    ]
                ]),
                parse_mode="HTML"
            )

            return

        buttons = []

        for sub in pending_subs:

            sid = str(
                sub["_id"]
            )

            buttons.append([
                InlineKeyboardButton(
                    f"User: {sub['user_id']}",
                    callback_data=(
                        f"sub_manage_{sid}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_home"
            )
        ])

        await query.edit_message_text(
            (
                "📥 <b>Pending Submissions</b>\n\n"
                "Select a submission to review:"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return
