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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters


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


# =========================
# HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

        self.wfile.write(
            b"TaskMint Bot is running!"
        )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server started on port {port}"
    )

    server.serve_forever()


# =========================
# MONGODB
# =========================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client[
    DATABASE_NAME
]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]
submissions_collection = db["task_submissions"]
settings_collection = db["settings"]


def now_utc():
    return datetime.now(
        timezone.utc
    )


def setup_database():

    users_collection.create_index(
        "user_id",
        unique=True
    )

    tasks_collection.create_index(
        "task_id",
        unique=True
    )

    withdrawals_collection.create_index(
        "user_id"
    )

    withdrawals_collection.create_index(
        "status"
    )

    submissions_collection.create_index(
        "user_id"
    )

    submissions_collection.create_index(
        "task_id"
    )

    submissions_collection.create_index(
        "status"
    )

    settings_collection.create_index(
        "key",
        unique=True
    )

    print(
        "MongoDB database ready."
    )


def get_setting(
    key,
    default_value
):

    item = settings_collection.find_one(
        {"key": key}
    )

    if item:
        return item.get(
            "value",
            default_value
        )

    return default_value


def update_setting(
    key,
    value
):

    settings_collection.update_one(
        {"key": key},
        {
            "$set": {
                "value": value
            }
        },
        upsert=True
    )


# =========================
# USER FUNCTIONS
# =========================

def get_user(user_id):

    return users_collection.find_one(
        {
            "user_id": user_id
        }
    )


def get_balance(user_id):

    user = get_user(user_id)

    if not user:
        return 0.0

    return float(
        user.get(
            "balance",
            0.0
        )
    )


def create_or_update_user(
    user,
    referrer_id=None
):

    current = users_collection.find_one(
        {
            "user_id": user.id
        }
    )

    now = now_utc()

    if not current:

        referred_by = None

        if (
            referrer_id
            and referrer_id != user.id
        ):

            ref_user = get_user(
                referrer_id
            )

            if ref_user:

                referred_by = referrer_id

                users_collection.update_one(
                    {
                        "user_id": referrer_id
                    },
                    {
                        "$inc": {
                            "referrals": 1
                        }
                    }
                )

        users_collection.update_one(
            {
                "user_id": user.id
            },
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
                    "referred_by": referred_by,
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
            {
                "user_id": user.id
            },
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                }
            }
        )


# =========================
# REFERRAL
# =========================

async def check_and_pay_referral(
    user_id,
    context
):

    user = get_user(
        user_id
    )

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

    completed = user.get(
        "completed_tasks",
        []
    )

    if not referred_by:
        return

    if len(completed) < 4:
        return

    commission = float(
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
        {
            "user_id": referred_by
        },
        {
            "$inc": {
                "balance": commission
            }
        }
    )

    try:

        await context.bot.send_message(
            referred_by,
            (
                "🎉 <b>Referral Bonus Unlocked!</b>\n\n"
                "Your referred user completed 4 tasks.\n"
                f"💰 You received <b>+{commission} "
                f"{TOKEN_NAME}</b>."
            ),
            parse_mode="HTML"
        )

    except Exception:
        pass


# =========================
# MAIN MENU
# =========================

def main_menu(user_id):

    buttons = [
        ["🎯 Tasks"],
        ["💰 Balance", "💳 Withdraw"],
        ["👥 Refer"]
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

    return InlineKeyboardMarkup(
        [
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
        ]
    )


# =========================
# CHANNEL CHECK
# =========================

async def is_channel_member(
    bot,
    user_id
):

    try:

        member = await bot.get_chat_member(
            REQUIRED_CHANNEL,
            user_id
        )

        return member.status in {
            "member",
            "administrator",
            "creator"
        }

    except Exception as error:

        print(
            "Channel check error:",
            error
        )

        return False


# =========================
# START
# =========================

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

    db_user = get_user(
        user.id
    )

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

    if not await is_channel_member(
        context.bot,
        user.id
    ):

        await update.message.reply_text(
            (
                "👋 <b>Hi dear user!</b>\n\n"
                "Please join our official channel, "
                "then press <b>Done</b>."
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
        user_id,
        (
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(
            user_id
        ),
        parse_mode="HTML"
    )


async def check_join(
    update,
    context
):

    query = update.callback_query

    user_id = query.from_user.id

    if not await is_channel_member(
        context.bot,
        user_id
    ):

        await query.answer(
            "❌ Please join the channel first.",
            show_alert=True
        )

        return

    await query.answer(
        "✅ Verified!"
    )

    await query.edit_message_text(
        (
            "✅ <b>Done!</b>\n\n"
            "Your membership has been verified."
        ),
        parse_mode="HTML"
    )

    await send_main_menu(
        context.bot,
        user_id
    )
# =========================
# BALANCE
# =========================

async def balance_menu(
    update,
    context
):

    balance = get_balance(
        update.effective_user.id
    )

    await update.message.reply_text(
        (
            "💰 <b>Your Balance</b>\n\n"
            f"💎 Balance: <b>{balance:.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        parse_mode="HTML"
    )


# =========================
# TASK MENU
# =========================

async def tasks_menu(
    update,
    context
):

    tasks = list(
        tasks_collection.find(
            {
                "active": True
            }
        ).sort(
            "created_at",
            -1
        )
    )

    buttons = []

    for task in tasks:

        total = int(
            task.get(
                "total_slots",
                0
            ) or 0
        )

        completed = int(
            task.get(
                "completed_slots",
                0
            ) or 0
        )

        if (
            total > 0
            and completed >= total
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

        buttons.append(
            [
                InlineKeyboardButton(
                    (
                        "🎯 "
                        + escape(
                            str(
                                task.get(
                                    "title",
                                    "Task"
                                )
                            )
                        )
                    ),
                    callback_data=(
                        f"task_{task['task_id']}"
                    )
                )
            ]
        )

    if not buttons:

        await update.message.reply_text(
            (
                "🎯 <b>Tasks</b>\n\n"
                "No tasks are available right now."
            ),
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


# =========================
# TASK DETAILS
# =========================

async def task_details(
    update,
    context
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

    total = int(
        task.get(
            "total_slots",
            0
        ) or 0
    )

    completed = int(
        task.get(
            "completed_slots",
            0
        ) or 0
    )

    if total <= 0:

        remaining = "Unlimited"

    else:

        remaining = max(
            total - completed,
            0
        )

    mode = task.get(
        "task_mode",
        "auto"
    )

    keyboard = []

    if task.get("link"):

        keyboard.append(
            [
                InlineKeyboardButton(
                    "🔗 Open Task Link",
                    url=task["link"]
                )
            ]
        )

    if mode == "auto":

        keyboard.append(
            [
                InlineKeyboardButton(
                    "✅ Verify & Complete",
                    callback_data=(
                        f"complete_{task_id}"
                    )
                )
            ]
        )

    else:

        keyboard.append(
            [
                InlineKeyboardButton(
                    "📤 Submit Proof",
                    callback_data=(
                        f"submitproof_{task_id}"
                    )
                )
            ]
        )

    await query.answer()

    await query.edit_message_text(
        (
            f"🎯 <b>{escape(str(task.get('title', 'Task')))}</b>\n\n"
            f"📝 {escape(str(task.get('description', '')))}\n\n"
            f"🔹 Type: <b>{escape(str(task.get('category', 'Custom')))}</b>\n"
            f"🔐 Verification: <b>{mode.upper()}</b>\n"
            f"💰 Reward: <b>{float(task.get('reward', 0)):.6f} {TOKEN_NAME}</b>\n"
            f"👥 Slots remaining: <b>{remaining}</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML"
    )


# =========================
# TASK SLOT RESERVATION
# =========================

async def reserve_task_slot(
    task_id
):

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:
        return False, None

    total = int(
        task.get(
            "total_slots",
            0
        ) or 0
    )

    if total > 0:

        result = tasks_collection.update_one(
            {
                "task_id": task_id,
                "active": True,
                "$expr": {
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
            },
            {
                "$inc": {
                    "completed_slots": 1
                }
            }
        )

    else:

        result = tasks_collection.update_one(
            {
                "task_id": task_id,
                "active": True
            },
            {
                "$inc": {
                    "completed_slots": 1
                }
            }
        )

    if result.modified_count != 1:

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

        return False, task

    task = tasks_collection.find_one(
        {
            "task_id": task_id
        }
    )

    if (
        total > 0
        and int(
            task.get(
                "completed_slots",
                0
            )
        ) >= total
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

    return True, task


def rollback_task_slot(
    task_id
):

    tasks_collection.update_one(
        {
            "task_id": task_id,
            "completed_slots": {
                "$gt": 0
            }
        },
        {
            "$inc": {
                "completed_slots": -1
            },
            "$set": {
                "active": True
            }
        }
    )


# =========================
# AUTO TASK COMPLETE
# =========================

async def complete_task(
    update,
    context
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

    if (
        not task
        or task.get(
            "task_mode",
            "auto"
        ) != "auto"
    ):

        await query.answer(
            "❌ This task is not available for automatic verification.",
            show_alert=True
        )

        return

    if users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    ):

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    category = task.get(
        "category",
        "Telegram Channel Join"
    )

    # Only Telegram Channel Join uses auto verification
    if category == "Telegram Channel Join":

        link = str(
            task.get(
                "link",
                ""
            )
        ).strip()

        parsed = urlparse(
            link
        )

        path = (
            parsed.path.strip(
                "/"
            ).split(
                "/"
            )[0]
            if parsed.path
            else ""
        )

        valid_prefixes = (
            "https://t.me/",
            "http://t.me/",
            "https://telegram.me/",
            "http://telegram.me/"
        )

        if (
            not path
            or not link.startswith(
                valid_prefixes
            )
        ):

            await query.answer(
                "❌ Invalid Telegram channel link.",
                show_alert=True
            )

            return

        try:

            member = await context.bot.get_chat_member(
                "@" + path,
                user_id
            )

            if member.status not in {
                "member",
                "administrator",
                "creator"
            }:

                await query.answer(
                    "❌ Join the target channel first.",
                    show_alert=True
                )

                return

        except Exception:

            await query.answer(
                "❌ Could not verify this Telegram task.",
                show_alert=True
            )

            return

    else:

        await query.answer(
            "❌ Automatic verification is only for Telegram Channel Join tasks.",
            show_alert=True
        )

        return

    reserved, _ = await reserve_task_slot(
        task_id
    )

    if not reserved:

        await query.answer(
            "❌ Task slots are finished.",
            show_alert=True
        )

        return

    reward = float(
        task.get(
            "reward",
            0
        )
    )

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

        rollback_task_slot(
            task_id
        )

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    await check_and_pay_referral(
        user_id,
        context
    )

    await query.answer(
        "✅ Task completed!",
        show_alert=True
    )

    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward: <b>+{reward:.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        parse_mode="HTML"
    )


# =========================
# MANUAL PROOF REQUEST
# =========================

async def request_proof_input(
    update,
    context
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
            "active": True,
            "task_mode": "manual"
        }
    )

    if not task:

        await query.answer(
            "❌ Task is not available.",
            show_alert=True
        )

        return

    if users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    ):

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    pending = submissions_collection.find_one(
        {
            "user_id": user_id,
            "task_id": task_id,
            "status": {
                "$in": [
                    "pending",
                    "processing"
                ]
            }
        }
    )

    if pending:

        await query.answer(
            "⏳ You already have a pending submission.",
            show_alert=True
        )

        return

    context.user_data[
        "submitting_task_id"
    ] = task_id

    await query.answer()

    await query.edit_message_text(
        (
            "📤 <b>Submit Proof</b>\n\n"
            f"Task: <b>{escape(str(task.get('title')))}</b>\n\n"
            "Send text, link, or screenshot proof."
        ),
        parse_mode="HTML"
        )
# =========================
# REFERRAL MENU
# =========================

async def refer_menu(
    update,
    context
):

    user_id = update.effective_user.id

    user = get_user(
        user_id
    ) or {}

    bot_info = await context.bot.get_me()

    link = (
        f"https://t.me/"
        f"{bot_info.username}"
        f"?start={user_id}"
    )

    commission = float(
        get_setting(
            "ref_commission",
            DEFAULT_REF_COMMISSION
        )
    )

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{user.get('referrals', 0)}</b>\n"
            f"🎁 Commission: <b>{commission} {TOKEN_NAME}</b>\n\n"
            f"🔗 <code>{escape(link)}</code>"
        ),
        parse_mode="HTML"
    )


# =========================
# WITHDRAW
# =========================

async def withdraw_menu(
    update,
    context
):

    user_id = update.effective_user.id

    balance = get_balance(
        user_id
    )

    minimum = float(
        get_setting(
            "min_withdraw",
            DEFAULT_MIN_WITHDRAW
        )
    )

    context.user_data.clear()

    if balance < minimum:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: <b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: <b>{minimum:.6f} POL</b>\n\n"
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
            f"📌 Minimum: <b>{minimum:.6f} POL</b>\n\n"
            "Enter withdrawal amount:"
        ),
        parse_mode="HTML"
    )


async def process_withdraw(
    update,
    context
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    step = context.user_data.get(
        "withdraw_step"
    )

    minimum = float(
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

        balance = get_balance(
            user_id
        )

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount must be greater than 0."
            )

            return

        if amount < minimum:

            await update.message.reply_text(
                (
                    f"❌ Minimum withdrawal is "
                    f"{minimum} POL."
                )
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

        # Atomic balance deduction
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

        withdrawal = {
            "user_id": user_id,
            "amount": amount,
            "token": TOKEN_NAME,
            "wallet": wallet,
            "status": "pending",
            "created_at": now_utc(),
            "updated_at": now_utc()
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

        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Withdrawal Submitted</b>\n\n"
                f"🆔 ID: <code>{result.inserted_id}</code>\n"
                f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                f"👛 Wallet: <code>{escape(wallet)}</code>\n"
                "📌 Status: <b>Pending</b>"
            ),
            parse_mode="HTML"
        )


# =========================
# ADMIN TASK TYPES
# =========================

ADMIN_TASK_TYPES = {

    "tasktype_telegram": (
        "Telegram Channel Join",
        "auto"
    ),

    "tasktype_x": (
        "X",
        "manual"
    ),

    "tasktype_instagram": (
        "Instagram",
        "manual"
    ),

    "tasktype_botjoin": (
        "Bot Join",
        "manual"
    ),

    "tasktype_custom": (
        "Custom/Link",
        "manual"
    )
}


def admin_keyboard():

    return InlineKeyboardMarkup(
        [
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
        ]
    )


def task_type_keyboard():

    return InlineKeyboardMarkup(
        [

            [
                InlineKeyboardButton(
                    "📢 Telegram Channel Join",
                    callback_data="tasktype_telegram"
                )
            ],

            [
                InlineKeyboardButton(
                    "𝕏 X",
                    callback_data="tasktype_x"
                ),
                InlineKeyboardButton(
                    "📸 Instagram",
                    callback_data="tasktype_instagram"
                )
            ],

            [
                InlineKeyboardButton(
                    "🤖 Bot Join",
                    callback_data="tasktype_botjoin"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔗 Custom/Link",
                    callback_data="tasktype_custom"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="admin_tasks"
                )
            ]

        ]
    )


def admin_tasks_keyboard():

    return InlineKeyboardMarkup(
        [

            [
                InlineKeyboardButton(
                    "➕ Add Task",
                    callback_data="task_add"
                )
            ],

            [
                InlineKeyboardButton(
                    "📋 View/Manage Tasks",
                    callback_data="task_list_admin"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="admin_home"
                )
            ]

        ]
    )


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update,
    context
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


async def show_admin_tasks(
    query
):

    active = tasks_collection.count_documents(
        {
            "active": True
        }
    )

    inactive = tasks_collection.count_documents(
        {
            "active": False
        }
    )

    await query.edit_message_text(
        (
            "🎯 <b>Manage Tasks</b>\n\n"
            f"🟢 Active: <b>{active}</b>\n"
            f"🔴 Inactive: <b>{inactive}</b>"
        ),
        reply_markup=admin_tasks_keyboard(),
        parse_mode="HTML"
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized.",
            show_alert=True
        )

        return

    data = query.data

    await query.answer()


    # =====================
    # ADMIN HOME
    # =====================

    if data == "admin_home":

        await query.edit_message_text(
            (
                "👑 <b>Admin Panel</b>\n\n"
                "Select an option:"
            ),
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )

        return


    # =====================
    # STATISTICS
    # =====================

    if data == "admin_stats":

        total_users = users_collection.count_documents({})

        total_tasks = tasks_collection.count_documents({})

        pending_withdrawals = withdrawals_collection.count_documents(
            {
                "status": "pending"
            }
        )

        pending_submissions = submissions_collection.count_documents(
            {
                "status": "pending"
            }
        )

        await query.edit_message_text(
            (
                "📊 <b>Statistics</b>\n\n"
                f"👥 Users: <b>{total_users}</b>\n"
                f"🎯 Tasks: <b>{total_tasks}</b>\n"
                f"🕐 Pending Withdrawals: "
                f"<b>{pending_withdrawals}</b>\n"
                f"📥 Pending Submissions: "
                f"<b>{pending_submissions}</b>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="admin_home"
                        )
                    ]
                ]
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # TASK MANAGEMENT
    # =====================

    if data == "admin_tasks":

        await show_admin_tasks(
            query
        )

        return


    # =====================
    # ADD TASK
    # =====================

    if data == "task_add":

        context.user_data.clear()

        context.user_data[
            "admin_action"
        ] = "task_type"

        await query.edit_message_text(
            (
                "➕ <b>Add New Task</b>\n\n"
                "Choose task type:"
            ),
            reply_markup=task_type_keyboard(),
            parse_mode="HTML"
        )

        return


    # =====================
    # TASK TYPE SELECT
    # =====================

    if data in ADMIN_TASK_TYPES:

        category, mode = ADMIN_TASK_TYPES[
            data
        ]

        context.user_data[
            "new_task"
        ] = {
            "category": category,
            "task_mode": mode
        }

        context.user_data[
            "admin_action"
        ] = "task_add_title"

        await query.edit_message_text(
            (
                f"✅ Type: <b>{category}</b>\n\n"
                "Send task title:"
            ),
            parse_mode="HTML"
        )

        return
