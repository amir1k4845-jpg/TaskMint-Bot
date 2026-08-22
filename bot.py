import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient
from pymongo.errors import PyMongoError

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

MIN_WITHDRAW = 1.0

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
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server started on port {port}"
    )

    server.serve_forever()


# =========================================================
# MONGODB
# =========================================================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]


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

    print("MongoDB database ready.")
    
# =========================================================
# USER FUNCTIONS
# =========================================================

def create_or_update_user(user, referrer_id=None):

    now = datetime.now(timezone.utc)

    existing_user = users_collection.find_one({"user_id": user.id})

    if not existing_user:
        ref_by = None
        if referrer_id and referrer_id != user.id:
            ref_user = users_collection.find_one({"user_id": referrer_id})
            if ref_user:
                ref_by = referrer_id
                users_collection.update_one(
                    {"user_id": referrer_id},
                    {
                        "$inc": {"referrals": 1, "balance": 0.1}
                    }
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
    return users_collection.find_one({"user_id": user_id})


def get_balance(user_id):
    user = get_user(user_id)
    if not user:
        return 0.0
    return float(user.get("balance", 0.0))


def main_menu(user_id):
    buttons = [
        ["🎯 Tasks"],
        ["💰 Balance", "💳 Withdraw"],
        ["👥 Refer"],
    ]
    if user_id == ADMIN_ID:
        buttons.append(["👑 Admin Panel"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Done", callback_data="check_join")]
    ])


async def is_channel_member(bot, user_id):
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as error:
        print("Channel check error:", error)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referrer_id = None
    if context.args:
        try:
            referrer_id = int(context.args[0])
        except ValueError:
            pass

    create_or_update_user(user, referrer_id)

    db_user = get_user(user.id)
    if db_user and db_user.get("is_banned", False):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return

    context.user_data.clear()

    await update.message.reply_text(
        "Checking...",
        reply_markup=ReplyKeyboardRemove()
    )

    member = await is_channel_member(context.bot, user.id)

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

    await send_main_menu(context.bot, user.id)


async def send_main_menu(bot, user_id):
    await bot.send_message(
        chat_id=user_id,
        text=(
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    member = await is_channel_member(context.bot, user_id)

    if not member:
        await query.answer("❌ Please join the channel first.", show_alert=True)
        return

    await query.answer("✅ Verified!")
    await query.edit_message_text(
        ("✅ <b>Done!</b>\n\nYour membership has been verified."),
        parse_mode="HTML"
    )
    await send_main_menu(context.bot, user_id)
               
