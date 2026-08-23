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
