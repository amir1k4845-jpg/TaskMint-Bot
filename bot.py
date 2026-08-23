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
