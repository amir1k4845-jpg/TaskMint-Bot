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

    # Telegram Channel Join and Telegram Group Join use auto verification
    if category in {"Telegram Channel Join", "Telegram Group Join"}:

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
    # =====================
    # TASK LIST
    # =====================

    if data == "task_list_admin":

        tasks = list(
            tasks_collection.find(
                {}
            ).sort(
                "created_at",
                -1
            ).limit(20)
        )

        if not tasks:

            await query.edit_message_text(
                (
                    "📋 <b>Task List</b>\n\n"
                    "No tasks found."
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 Back",
                                callback_data="admin_tasks"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML"
            )

            return

        buttons = []

        for task in tasks:

            status = (
                "🟢"
                if task.get("active")
                else "🔴"
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        (
                            f"{status} "
                            f"{task.get('title', 'Task')}"
                        ),
                        callback_data=(
                            f"admintask_view_"
                            f"{task['task_id']}"
                        )
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="admin_tasks"
                )
            ]
        )

        await query.edit_message_text(
            (
                "📋 <b>Tasks</b>\n\n"
                "Select a task:"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # VIEW TASK
    # =====================

    if data.startswith(
        "admintask_view_"
    ):

        tid = data.replace(
            "admintask_view_",
            "",
            1
        )

        task = tasks_collection.find_one(
            {
                "task_id": tid
            }
        )

        if not task:

            await query.answer(
                "❌ Task not found.",
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

        slot_text = (
            "Unlimited"
            if total <= 0
            else f"{completed}/{total}"
        )

        await query.edit_message_text(
            (
                f"🎯 <b>{escape(str(task.get('title')))}</b>\n\n"
                f"📝 {escape(str(task.get('description')))}\n"
                f"🔗 {escape(str(task.get('link')))}\n"
                f"🔹 Type: <b>{escape(str(task.get('category')))}</b>\n"
                f"🔐 Verification: "
                f"<b>{task.get('task_mode', 'manual').upper()}</b>\n"
                f"💰 Reward: "
                f"<b>{float(task.get('reward', 0)):.6f} {TOKEN_NAME}</b>\n"
                f"👥 Slots: <b>{slot_text}</b>\n"
                f"📌 Status: "
                f"<b>{'Active' if task.get('active') else 'Inactive'}</b>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [

                    [
                        InlineKeyboardButton(
                            (
                                "⏸ Deactivate"
                                if task.get("active")
                                else
                                "▶️ Activate"
                            ),
                            callback_data=(
                                f"admintask_toggle_{tid}"
                            )
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "❌ Delete",
                            callback_data=(
                                f"admintask_del_{tid}"
                            )
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="task_list_admin"
                        )
                    ]

                ]
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # TOGGLE TASK
    # =====================

    if data.startswith(
        "admintask_toggle_"
    ):

        tid = data.replace(
            "admintask_toggle_",
            "",
            1
        )

        task = tasks_collection.find_one(
            {
                "task_id": tid
            }
        )

        if task:

            tasks_collection.update_one(
                {
                    "task_id": tid
                },
                {
                    "$set": {
                        "active": not bool(
                            task.get(
                                "active"
                            )
                        )
                    }
                }
            )

        await query.answer(
            "✅ Status updated!",
            show_alert=True
        )

        return


    # =====================
    # DELETE TASK
    # =====================

    if data.startswith(
        "admintask_del_"
    ):

        tid = data.replace(
            "admintask_del_",
            "",
            1
        )

        tasks_collection.delete_one(
            {
                "task_id": tid
            }
        )

        await query.answer(
            "✅ Task deleted.",
            show_alert=True
        )

        await show_admin_tasks(
            query
        )

        return


    # =====================
    # WITHDRAWALS
    # =====================

    if data == "admin_withdrawals":

        items = list(
            withdrawals_collection.find(
                {
                    "status": "pending"
                }
            ).sort(
                "created_at",
                -1
            ).limit(20)
        )

        if not items:

            await query.edit_message_text(
                (
                    "💳 <b>Pending Withdrawals</b>\n\n"
                    "No pending withdrawals."
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

        buttons = []

        for withdrawal in items:

            buttons.append(
                [
                    InlineKeyboardButton(
                        (
                            f"User {withdrawal['user_id']} "
                            f"• {withdrawal['amount']} "
                            f"{TOKEN_NAME}"
                        ),
                        callback_data=(
                            f"wd_manage_"
                            f"{withdrawal['_id']}"
                        )
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="admin_home"
                )
            ]
        )

        await query.edit_message_text(
            (
                "💳 <b>Pending Withdrawals</b>\n\n"
                "Select one:"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # WITHDRAWAL DETAILS
    # =====================

    if data.startswith(
        "wd_manage_"
    ):

        try:

            wid = ObjectId(
                data.replace(
                    "wd_manage_",
                    "",
                    1
                )
            )

            withdrawal = withdrawals_collection.find_one(
                {
                    "_id": wid,
                    "status": "pending"
                }
            )

        except Exception:

            withdrawal = None

        if not withdrawal:

            await query.answer(
                "❌ Withdrawal not found or already processed.",
                show_alert=True
            )

            return

        await query.edit_message_text(
            (
                "💳 <b>Withdrawal Details</b>\n\n"
                f"👤 User: "
                f"<code>{withdrawal['user_id']}</code>\n"
                f"💰 Amount: "
                f"<b>{withdrawal['amount']} {TOKEN_NAME}</b>\n"
                f"👛 Wallet: "
                f"<code>{escape(str(withdrawal['wallet']))}</code>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [

                    [
                        InlineKeyboardButton(
                            "✅ Mark Paid",
                            callback_data=(
                                f"wd_pay_{withdrawal['_id']}"
                            )
                        ),

                        InlineKeyboardButton(
                            "❌ Reject & Refund",
                            callback_data=(
                                f"wd_ref_{withdrawal['_id']}"
                            )
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="admin_withdrawals"
                        )
                    ]

                ]
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # MARK WITHDRAWAL PAID
    # =====================

    if data.startswith(
        "wd_pay_"
    ):

        try:

            wid = ObjectId(
                data.replace(
                    "wd_pay_",
                    "",
                    1
                )
            )

            result = withdrawals_collection.update_one(
                {
                    "_id": wid,
                    "status": "pending"
                },
                {
                    "$set": {
                        "status": "paid",
                        "updated_at": now_utc()
                    }
                }
            )

            if result.modified_count != 1:

                await query.answer(
                    "⚠️ Already processed.",
                    show_alert=True
                )

                return

            withdrawal = withdrawals_collection.find_one(
                {
                    "_id": wid
                }
            )

            if withdrawal:

                try:

                    await context.bot.send_message(
                        withdrawal["user_id"],
                        (
                            "✅ Your withdrawal of "
                            f"<b>{withdrawal['amount']} "
                            f"{TOKEN_NAME}</b> has been paid!"
                        ),
                        parse_mode="HTML"
                    )

                except Exception:
                    pass

            await query.answer(
                "✅ Marked as paid!",
                show_alert=True
            )

            await query.edit_message_text(
                (
                    "✅ <b>Withdrawal marked as paid.</b>"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 Withdrawals",
                                callback_data="admin_withdrawals"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML"
            )

        except Exception:

            await query.answer(
                "❌ Error.",
                show_alert=True
            )

        return


    # =====================
    # REJECT WITHDRAWAL
    # =====================

    if data.startswith(
        "wd_ref_"
    ):

        try:

            wid = ObjectId(
                data.replace(
                    "wd_ref_",
                    "",
                    1
                )
            )

            withdrawal = withdrawals_collection.find_one_and_update(
                {
                    "_id": wid,
                    "status": "pending"
                },
                {
                    "$set": {
                        "status": "rejected",
                        "updated_at": now_utc()
                    }
                }
            )

            if not withdrawal:

                await query.answer(
                    "⚠️ Already processed.",
                    show_alert=True
                )

                return

            users_collection.update_one(
                {
                    "user_id": withdrawal["user_id"]
                },
                {
                    "$inc": {
                        "balance": withdrawal["amount"]
                    }
                }
            )

            try:

                await context.bot.send_message(
                    withdrawal["user_id"],
                    (
                        "❌ Your withdrawal of "
                        f"<b>{withdrawal['amount']} "
                        f"{TOKEN_NAME}</b> was rejected "
                        "and refunded."
                    ),
                    parse_mode="HTML"
                )

            except Exception:
                pass

            await query.answer(
                "✅ Rejected and refunded!",
                show_alert=True
            )

            await query.edit_message_text(
                (
                    "❌ <b>Withdrawal rejected and refunded.</b>"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 Withdrawals",
                                callback_data="admin_withdrawals"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML"
            )

        except Exception:

            await query.answer(
                "❌ Error.",
                show_alert=True
            )

        return
    # =====================
    # SUBMISSIONS
    # =====================

    if data == "admin_submissions":

        submissions = list(
            submissions_collection.find(
                {
                    "status": "pending"
                }
            ).sort(
                "created_at",
                -1
            ).limit(20)
        )

        if not submissions:

            await query.edit_message_text(
                (
                    "📥 <b>Pending Submissions</b>\n\n"
                    "No pending submissions."
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

        buttons = []

        for submission in submissions:

            buttons.append(
                [
                    InlineKeyboardButton(
                        (
                            f"User {submission['user_id']} "
                            f"• {submission['task_id']}"
                        ),
                        callback_data=(
                            f"sub_manage_"
                            f"{submission['_id']}"
                        )
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="admin_home"
                )
            ]
        )

        await query.edit_message_text(
            (
                "📥 <b>Pending Submissions</b>\n\n"
                "Select one:"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # SUBMISSION DETAILS
    # =====================

    if data.startswith(
        "sub_manage_"
    ):

        try:

            sid = ObjectId(
                data.replace(
                    "sub_manage_",
                    "",
                    1
                )
            )

            submission = submissions_collection.find_one(
                {
                    "_id": sid
                }
            )

        except Exception:

            submission = None

        if not submission:

            await query.answer(
                "❌ Submission not found.",
                show_alert=True
            )

            return

        task = tasks_collection.find_one(
            {
                "task_id": submission["task_id"]
            }
        )

        task_name = (
            task.get(
                "title",
                "Unknown"
            )
            if task
            else "Unknown"
        )

        proof = escape(
            str(
                submission.get(
                    "proof",
                    ""
                )
            )
        )

        submission_keyboard = InlineKeyboardMarkup(
            [

                [
                    InlineKeyboardButton(
                        "✅ Approve",
                        callback_data=(
                            f"sub_approve_{submission['_id']}"
                        )
                    ),

                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=(
                            f"sub_reject_{submission['_id']}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_submissions"
                    )
                ]

            ]
        )

        details_text = (
            "📥 <b>Submission Details</b>\n\n"
            f"👤 User: "
            f"<code>{submission['user_id']}</code>\n"
            f"🎯 Task: "
            f"<b>{escape(str(task_name))}</b>\n"
            f"📄 Proof: "
            f"<code>{proof}</code>"
        )

        photo_file_id = submission.get(
            "photo_file_id"
        )

        if photo_file_id:
            # The screenshot is stored as Telegram's file_id in MongoDB.
            # Send the actual photo to the admin instead of only showing
            # a text note that a screenshot exists.
            try:
                await query.edit_message_text(
                    "📥 <b>Submission Details</b>\n\n"
                    "📷 Screenshot proof is attached below.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 Back",
                                    callback_data="admin_submissions"
                                )
                            ]
                        ]
                    ),
                    parse_mode="HTML"
                )

                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo_file_id,
                    caption=details_text,
                    reply_markup=submission_keyboard,
                    parse_mode="HTML"
                )

            except Exception as error:
                print(
                    "Send submission screenshot error:",
                    error
                )

                await query.edit_message_text(
                    details_text + "\n\n⚠️ Could not display the screenshot.",
                    reply_markup=submission_keyboard,
                    parse_mode="HTML"
                )
        else:
            await query.edit_message_text(
                details_text,
                reply_markup=submission_keyboard,
                parse_mode="HTML"
            )

        return


    # =====================
    # APPROVE SUBMISSION
    # =====================

    if data.startswith(
        "sub_approve_"
    ):

        sid = data.replace(
            "sub_approve_",
            "",
            1
        )

        await approve_submission(
            query,
            context,
            sid
        )

        return


    # =====================
    # REJECT SUBMISSION
    # =====================

    if data.startswith(
        "sub_reject_"
    ):

        sid = data.replace(
            "sub_reject_",
            "",
            1
        )

        await reject_submission(
            query,
            context,
            sid
        )

        return


    # =====================
    # USER MANAGEMENT
    # =====================

    if data == "admin_users":

        total = users_collection.count_documents(
            {}
        )

        await query.edit_message_text(
            (
                "👥 <b>User Management</b>\n\n"
                f"Total Users: <b>{total}</b>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [

                    [
                        InlineKeyboardButton(
                            "🚫 Ban User",
                            callback_data="user_ban"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🔓 Unban User",
                            callback_data="user_unban"
                        )
                    ],

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


    if data == "user_ban":

        context.user_data[
            "admin_action"
        ] = "user_ban_action"

        await query.edit_message_text(
            "🚫 Send User ID to ban:",
            parse_mode="HTML"
        )

        return


    if data == "user_unban":

        context.user_data[
            "admin_action"
        ] = "user_unban_action"

        await query.edit_message_text(
            "🔓 Send User ID to unban:",
            parse_mode="HTML"
        )

        return


    # =====================
    # BALANCE MANAGEMENT
    # =====================

    if data == "admin_balance":

        await query.edit_message_text(
            "💰 <b>Balance Management</b>",
            reply_markup=InlineKeyboardMarkup(
                [

                    [
                        InlineKeyboardButton(
                            "➕ Add POL",
                            callback_data="balance_add"
                        ),

                        InlineKeyboardButton(
                            "➖ Remove POL",
                            callback_data="balance_remove"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🔎 Check Balance",
                            callback_data="balance_check"
                        )
                    ],

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


    if data == "balance_add":

        context.user_data[
            "admin_action"
        ] = "balance_add"

        await query.edit_message_text(
            (
                "➕ <b>Add POL</b>\n\n"
                "Send:\n"
                "<code>USER_ID AMOUNT</code>"
            ),
            parse_mode="HTML"
        )

        return


    if data == "balance_remove":

        context.user_data[
            "admin_action"
        ] = "balance_remove"

        await query.edit_message_text(
            (
                "➖ <b>Remove POL</b>\n\n"
                "Send:\n"
                "<code>USER_ID AMOUNT</code>"
            ),
            parse_mode="HTML"
        )

        return


    if data == "balance_check":

        context.user_data[
            "admin_action"
        ] = "balance_check"

        await query.edit_message_text(
            (
                "🔎 <b>Check Balance</b>\n\n"
                "Send User ID."
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # SETTINGS
    # =====================

    if data == "admin_settings":

        minimum = float(
            get_setting(
                "min_withdraw",
                DEFAULT_MIN_WITHDRAW
            )
        )

        commission = float(
            get_setting(
                "ref_commission",
                DEFAULT_REF_COMMISSION
            )
        )

        await query.edit_message_text(
            (
                "⚙️ <b>Bot Settings</b>\n\n"
                f"📌 Min Withdraw: "
                f"<b>{minimum} {TOKEN_NAME}</b>\n"
                f"🎁 Ref Commission: "
                f"<b>{commission} {TOKEN_NAME}</b>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [

                    [
                        InlineKeyboardButton(
                            "✏️ Change Min Withdraw",
                            callback_data="set_min_withdraw"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "✏️ Change Ref Commission",
                            callback_data="set_ref_comm"
                        )
                    ],

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


    if data == "set_min_withdraw":

        context.user_data[
            "admin_action"
        ] = "update_min_withdraw"

        await query.edit_message_text(
            (
                "📌 Send new minimum "
                "withdraw amount:"
            ),
            parse_mode="HTML"
        )

        return


    if data == "set_ref_comm":

        context.user_data[
            "admin_action"
        ] = "update_ref_comm"

        await query.edit_message_text(
            (
                "🎁 Send new referral "
                "commission amount:"
            ),
            parse_mode="HTML"
        )

        return


    # =====================
    # BROADCAST
    # =====================

    if data == "admin_broadcast":

        context.user_data[
            "admin_action"
        ] = "admin_broadcast_msg"

        await query.edit_message_text(
            (
                "📢 <b>Broadcast</b>\n\n"
                "Send the message to broadcast:"
            ),
            parse_mode="HTML"
        )

        return


# =========================
# APPROVE SUBMISSION
# =========================

async def approve_submission(
    query,
    context,
    sid
):

    try:

        oid = ObjectId(
            sid
        )

        submission = submissions_collection.find_one_and_update(
            {
                "_id": oid,
                "status": "pending"
            },
            {
                "$set": {
                    "status": "processing",
                    "reviewed_at": now_utc()
                }
            }
        )

        if not submission:

            await query.answer(
                "⚠️ Already processed.",
                show_alert=True
            )

            return

        user_id = submission[
            "user_id"
        ]

        task_id = submission[
            "task_id"
        ]

        if users_collection.find_one(
            {
                "user_id": user_id,
                "completed_tasks": task_id
            }
        ):

            submissions_collection.update_one(
                {
                    "_id": oid
                },
                {
                    "$set": {
                        "status": "rejected",
                        "reason": "Already completed"
                    }
                }
            )

            await query.answer(
                "⚠️ User already completed this task.",
                show_alert=True
            )

            return

        task = tasks_collection.find_one(
            {
                "task_id": task_id,
                "task_mode": "manual"
            }
        )

        if (
            not task
            or not task.get(
                "active",
                False
            )
        ):

            submissions_collection.update_one(
                {
                    "_id": oid
                },
                {
                    "$set": {
                        "status": "rejected",
                        "reason": "Task inactive"
                    }
                }
            )

            await query.answer(
                "❌ Task is inactive.",
                show_alert=True
            )

            return
        # Reserve one slot before giving reward
        reserved, _ = await reserve_task_slot(
            task_id
        )

        if not reserved:

            submissions_collection.update_one(
                {
                    "_id": oid
                },
                {
                    "$set": {
                        "status": "rejected",
                        "reason": "Slots finished"
                    }
                }
            )

            await query.answer(
                "❌ No task slots remaining.",
                show_alert=True
            )

            return

        reward = float(
            task.get(
                "reward",
                0
            )
        )

        # Reward only if user has not completed task
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

            submissions_collection.update_one(
                {
                    "_id": oid
                },
                {
                    "$set": {
                        "status": "rejected",
                        "reason": "Already completed"
                    }
                }
            )

            await query.answer(
                "⚠️ User already completed this task.",
                show_alert=True
            )

            return

        submissions_collection.update_one(
            {
                "_id": oid
            },
            {
                "$set": {
                    "status": "approved",
                    "updated_at": now_utc()
                }
            }
        )

        await check_and_pay_referral(
            user_id,
            context
        )

        try:

            await context.bot.send_message(
                user_id,
                (
                    f"✅ Your <b>{escape(str(task.get('category', 'manual')))}</b> "
                    "task was approved!\n\n"
                    f"💰 Reward: <b>+{reward:.6f} "
                    f"{TOKEN_NAME}</b>"
                ),
                parse_mode="HTML"
            )

        except Exception:
            pass

        await query.answer(
            "✅ Approved and reward added!",
            show_alert=True
        )

        await query.edit_message_text(
            "✅ <b>Submission approved.</b>",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Submissions",
                            callback_data="admin_submissions"
                        )
                    ]
                ]
            ),
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            "Approve submission error:",
            error
        )

        try:

            await query.answer(
                "❌ Error approving submission.",
                show_alert=True
            )

        except Exception:
            pass


# =========================
# REJECT SUBMISSION
# =========================

async def reject_submission(
    query,
    context,
    sid
):

    try:

        oid = ObjectId(
            sid
        )

        result = submissions_collection.update_one(
            {
                "_id": oid,
                "status": "pending"
            },
            {
                "$set": {
                    "status": "rejected",
                    "updated_at": now_utc()
                }
            }
        )

        if result.modified_count != 1:

            await query.answer(
                "⚠️ Already processed.",
                show_alert=True
            )

            return

        submission = submissions_collection.find_one(
            {
                "_id": oid
            }
        )

        if submission:

            try:

                await context.bot.send_message(
                    submission["user_id"],
                    "❌ Your task proof was rejected by admin."
                )

            except Exception:
                pass

        await query.answer(
            "❌ Submission rejected.",
            show_alert=True
        )

        await query.edit_message_text(
            "❌ <b>Submission rejected.</b>",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Submissions",
                            callback_data="admin_submissions"
                        )
                    ]
                ]
            ),
            parse_mode="HTML"
        )

    except Exception:

        await query.answer(
            "❌ Error.",
            show_alert=True
        )


# =========================
# ADMIN TEXT ACTIONS
# =========================

async def admin_text_action(
    update,
    context
):

    if update.effective_user.id != ADMIN_ID:
        return False

    action = context.user_data.get(
        "admin_action"
    )

    text = update.message.text.strip()


    # =====================
    # BROADCAST
    # =====================

    if action == "admin_broadcast_msg":

        context.user_data.clear()

        count = 0

        await update.message.reply_text(
            "📢 Broadcasting started..."
        )

        for user in users_collection.find(
            {
                "is_banned": {
                    "$ne": True
                }
            },
            {
                "user_id": 1
            }
        ):

            try:

                await context.bot.send_message(
                    user["user_id"],
                    text,
                    parse_mode="HTML"
                )

                count += 1

            except Exception:
                pass

        await update.message.reply_text(
            (
                f"✅ Broadcast complete. "
                f"Sent to {count} users."
            )
        )

        return True


    # =====================
    # TASK TITLE
    # =====================

    if action == "task_add_title":

        context.user_data[
            "new_task"
        ]["title"] = text

        context.user_data[
            "admin_action"
        ] = "task_add_desc"

        await update.message.reply_text(
            "📝 Send task description:"
        )

        return True


    # =====================
    # TASK DESCRIPTION
    # =====================

    if action == "task_add_desc":

        context.user_data[
            "new_task"
        ]["description"] = text

        context.user_data[
            "admin_action"
        ] = "task_add_link"

        await update.message.reply_text(
            "🔗 Send task link:"
        )

        return True


    # =====================
    # TASK LINK
    # =====================

    if action == "task_add_link":

        if not text.startswith(
            (
                "http://",
                "https://"
            )
        ):

            await update.message.reply_text(
                (
                    "❌ Send a valid URL "
                    "starting with http:// or https://"
                )
            )

            return True

        context.user_data[
            "new_task"
        ]["link"] = text

        context.user_data[
            "admin_action"
        ] = "task_add_reward"

        await update.message.reply_text(
            (
                f"💰 Send reward amount "
                f"in {TOKEN_NAME}:"
            )
        )

        return True


    # =====================
    # TASK REWARD
    # =====================

    if action == "task_add_reward":

        try:

            reward = float(
                text
            )

            if reward <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                (
                    "❌ Invalid reward. "
                    "Send a positive number:"
                )
            )

            return True

        context.user_data[
            "new_task"
        ]["reward"] = reward

        context.user_data[
            "admin_action"
        ] = "task_add_slots"

        await update.message.reply_text(
            (
                "👥 Send total slots.\n"
                "Use <code>0</code> for unlimited:"
            ),
            parse_mode="HTML"
        )

        return True


    # =====================
    # TASK SLOTS
    # =====================

    if action == "task_add_slots":

        try:

            slots = int(
                text
            )

            if slots < 0:
                raise ValueError

            task = context.user_data.get(
                "new_task",
                {}
            )

            if not task.get(
                "category"
            ):

                raise ValueError

            task_id = uuid.uuid4().hex[:10]

            tasks_collection.insert_one(
                {
                    "task_id": task_id,

                    "title": task["title"],

                    "description": task["description"],

                    "link": task["link"],

                    "reward": float(
                        task["reward"]
                    ),

                    "total_slots": slots,

                    "completed_slots": 0,

                    "category": task["category"],

                    "task_mode": task["task_mode"],

                    "active": True,

                    "created_at": now_utc()
                }
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Task added successfully!"
            )

        except Exception as error:

            print(
                "Task create error:",
                error
            )

            await update.message.reply_text(
                (
                    "❌ Could not create task. "
                    "Send slots again:"
                )
            )

        return True


    # =====================
    # SETTINGS
    # =====================

    if action in {
        "update_min_withdraw",
        "update_ref_comm"
    }:

        try:

            value = float(
                text
            )

            if value <= 0:
                raise ValueError

            if action == "update_min_withdraw":

                key = "min_withdraw"

            else:

                key = "ref_commission"

            update_setting(
                key,
                value
            )

            context.user_data.clear()

            await update.message.reply_text(
                (
                    f"✅ Setting updated to "
                    f"{value} {TOKEN_NAME}."
                )
            )

        except ValueError:

            await update.message.reply_text(
                (
                    "❌ Send a valid "
                    "positive number:"
                )
            )

        return True


    # =====================
    # BAN / UNBAN
    # =====================

    if action in {
        "user_ban_action",
        "user_unban_action"
    }:

        try:

            uid = int(
                text
            )

            banned = (
                action == "user_ban_action"
            )

            result = users_collection.update_one(
                {
                    "user_id": uid
                },
                {
                    "$set": {
                        "is_banned": banned
                    }
                }
            )

            context.user_data.clear()

            if result.matched_count:

                if banned:

                    message = (
                        f"✅ User {uid} banned."
                    )

                else:

                    message = (
                        f"✅ User {uid} unbanned."
                    )

            else:

                message = (
                    "❌ User not found."
                )

            await update.message.reply_text(
                message
            )

        except ValueError:

            await update.message.reply_text(
                "❌ Invalid User ID."
            )

        return True


    # =====================
    # BALANCE ADD / REMOVE
    # =====================

    if action in {
        "balance_add",
        "balance_remove"
    }:

        try:

            parts = text.split()

            uid = int(
                parts[0]
            )

            amount = float(
                parts[1]
            )

            if amount <= 0:
                raise ValueError

            if action == "balance_add":

                result = users_collection.update_one(
                    {
                        "user_id": uid
                    },
                    {
                        "$inc": {
                            "balance": amount
                        }
                    }
                )

                if result.matched_count:

                    message = (
                        f"✅ Added {amount} "
                        f"{TOKEN_NAME}."
                    )

                else:

                    message = (
                        "❌ User not found."
                    )

            else:

                result = users_collection.update_one(
                    {
                        "user_id": uid,
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

                if result.modified_count:

                    message = (
                        f"✅ Removed {amount} "
                        f"{TOKEN_NAME}."
                    )

                else:

                    message = (
                        "❌ User not found or "
                        "insufficient balance."
                    )

            context.user_data.clear()

            await update.message.reply_text(
                message
            )

        except (
            ValueError,
            IndexError
        ):

            await update.message.reply_text(
                (
                    "❌ Format:\n"
                    "USER_ID AMOUNT"
                )
            )

        return True


    # =====================
    # BALANCE CHECK
    # =====================

    if action == "balance_check":

        try:

            uid = int(
                text
            )

            user = get_user(
                uid
            )

            balance = get_balance(
                uid
            )

            context.user_data.clear()

            if user:

                await update.message.reply_text(
                    (
                        f"🔎 User "
                        f"<code>{uid}</code>\n\n"
                        f"💰 Balance: "
                        f"<b>{balance:.6f} "
                        f"{TOKEN_NAME}</b>"
                    ),
                    parse_mode="HTML"
                )

            else:

                await update.message.reply_text(
                    "❌ User not found."
                )

        except ValueError:

            await update.message.reply_text(
                "❌ Invalid User ID."
            )

        return True


    return False
# =========================
# MENU HANDLER
# =========================

async def menu_handler(
    update,
    context
):

    user_id = update.effective_user.id

    user = get_user(
        user_id
    )

    if (
        user
        and user.get(
            "is_banned",
            False
        )
    ):

        return

    text = update.message.text

    if text == "🎯 Tasks":

        await tasks_menu(
            update,
            context
        )

    elif text == "💰 Balance":

        await balance_menu(
            update,
            context
        )

    elif text == "💳 Withdraw":

        await withdraw_menu(
            update,
            context
        )

    elif text == "👥 Refer":

        await refer_menu(
            update,
            context
        )

    elif (
        text == "👑 Admin Panel"
        and user_id == ADMIN_ID
    ):

        await admin_panel(
            update,
            context
        )


# =========================
# SAVE MANUAL SUBMISSION
# =========================

async def save_submission(
    update,
    context,
    proof,
    photo_file_id=None
):

    user_id = update.effective_user.id

    task_id = context.user_data.get(
        "submitting_task_id"
    )

    if not task_id:
        return False

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True,
            "task_mode": "manual"
        }
    )

    if not task:

        context.user_data.pop(
            "submitting_task_id",
            None
        )

        await update.message.reply_text(
            "❌ This task is no longer available."
        )

        return True

    if users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    ):

        context.user_data.pop(
            "submitting_task_id",
            None
        )

        await update.message.reply_text(
            "❌ You already completed this task."
        )

        return True

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

        context.user_data.pop(
            "submitting_task_id",
            None
        )

        await update.message.reply_text(
            "⏳ You already have a pending submission."
        )

        return True

    submission = {
        "user_id": user_id,
        "task_id": task_id,
        "proof": proof,
        "status": "pending",
        "created_at": now_utc()
    }

    if photo_file_id:

        submission[
            "photo_file_id"
        ] = photo_file_id

    try:

        submissions_collection.insert_one(
            submission
        )

    except PyMongoError:

        await update.message.reply_text(
            "❌ Could not submit proof. Try again."
        )

        return True

    context.user_data.pop(
        "submitting_task_id",
        None
    )

    await update.message.reply_text(
        (
            "✅ Proof submitted successfully!\n"
            "Admin will review it."
        )
    )

    return True


# =========================
# PHOTO PROOF
# =========================

async def photo_proof_handler(
    update,
    context
):

    if context.user_data.get(
        "submitting_task_id"
    ):

        proof = (
            update.message.caption.strip()
            if update.message.caption
            else "Screenshot proof"
        )

        await save_submission(
            update,
            context,
            proof,
            update.message.photo[-1].file_id
        )


# =========================
# TEXT HANDLER
# =========================

async def text_handler(
    update,
    context
):

    user_id = update.effective_user.id

    if context.user_data.get(
        "submitting_task_id"
    ):

        await save_submission(
            update,
            context,
            update.message.text.strip()
        )

        return

    if (
        context.user_data.get(
            "admin_action"
        )
        and user_id == ADMIN_ID
    ):

        if await admin_text_action(
            update,
            context
        ):

            return

    if context.user_data.get(
        "withdraw_step"
    ):

        await process_withdraw(
            update,
            context
        )

        return

    await menu_handler(
        update,
        context
    )


# =========================
# CALLBACK ROUTER
# =========================

async def callback_router(
    update,
    context
):

    query = update.callback_query

    data = query.data or ""

    user_id = query.from_user.id


    # Global join verification
    if data == "check_join":

        await check_join(
            update,
            context
        )

        return


    # Admin callbacks first
    if user_id == ADMIN_ID:

        admin_prefixes = (
            "admin_",
            "task_add",
            "tasktype_",
            "task_list",
            "admintask_",
            "sub_",
            "wd_",
            "user_",
            "balance_",
            "set_"
        )

        if data.startswith(
            admin_prefixes
        ):

            await admin_callback(
                update,
                context
            )

            return


    # User task details
    if (
        data.startswith("task_")
        and data not in {
            "task_add",
            "task_list_admin"
        }
    ):

        await task_details(
            update,
            context
        )

        return


    # Auto task
    if data.startswith(
        "complete_"
    ):

        await complete_task(
            update,
            context
        )

        return


    # Manual task
    if data.startswith(
        "submitproof_"
    ):

        await request_proof_input(
            update,
            context
        )

        return


    try:

        await query.answer()

    except Exception:
        pass


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        repr(
            context.error
        )
    )


# =========================
# MAIN
# =========================

def main():

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    print(
        "Connecting to MongoDB..."
    )

    mongo_client.admin.command(
        "ping"
    )

    print(
        "MongoDB connected successfully."
    )

    setup_database()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_proof_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "TaskMint Bot is running "
        "with fixed task/admin system..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# =========================
# START BOT
# =========================

if __name__ == "__main__":

    main()
