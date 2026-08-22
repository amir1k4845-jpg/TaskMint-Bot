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

TOKEN_NAME = "POL"

MIN_WITHDRAW = 1.0

DATABASE_NAME = "taskmint"


# =========================================================
# CONFIG VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing."
    )

if not MONGO_URI:
    raise RuntimeError(
        "MONGO_URI environment variable is missing."
    )


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
        os.environ.get(
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


# =========================================================
# MONGODB
# =========================================================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client[DATABASE_NAME]

users_collection = db["users"]

withdrawals_collection = db["withdrawals"]

tasks_collection = db["tasks"]


# =========================================================
# DATABASE INDEXES
# =========================================================

def setup_database():

    users_collection.create_index(
        "user_id",
        unique=True
    )

    withdrawals_collection.create_index(
        "user_id"
    )

    withdrawals_collection.create_index(
        "status"
    )

    tasks_collection.create_index(
        "task_id",
        unique=True
    )

    print(
        "MongoDB indexes are ready."
    )


# =========================================================
# USER DATABASE FUNCTIONS
# =========================================================

def create_or_update_user(user):

    now = datetime.now(
        timezone.utc
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
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": user.id,
                "balance": 0.0,
                "referrals": 0,
                "referred_by": None,
                "completed_tasks": [],
                "daily_bonus_date": None,
                "created_at": now,
            }
        },
        upsert=True
    )


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


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(user_id):

    buttons = [
        [
            "🎯 Tasks"
        ],
        [
            "💳 Withdraw",
            "👥 Refer"
        ]
    ]

    if user_id == ADMIN_ID:

        buttons.append(
            [
                "👑 Admin Panel"
            ]
        )

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True
    )


# =========================================================
# REQUIRED CHANNEL KEYBOARD
# =========================================================

def join_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Join Channel",
                    url="https://t.me/TaskMint_v1"
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Verify Join",
                    callback_data="verify_join"
                )
            ]
        ]
    )


# =========================================================
# CHANNEL MEMBERSHIP CHECK
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
            "Channel membership check error:",
            error
        )

        return False


# =========================================================
# SEND JOIN MESSAGE
# =========================================================

async def send_join_message(
    update
):

    text = (
        "🔐 <b>Channel Join Required</b>\n\n"
        "You must join our official channel "
        "before using TaskMint.\n\n"
        "1️⃣ Join the channel\n"
        "2️⃣ Click Verify Join\n"
        "3️⃣ Access the bot"
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )


# =========================================================
# START COMMAND
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    create_or_update_user(user)

    is_member = await is_channel_member(
        context.bot,
        user.id
    )

    if not is_member:

        await send_join_message(
            update
        )

        return

    await update.message.reply_text(
        (
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option from the menu below."
        ),
        reply_markup=main_menu(user.id),
        parse_mode="HTML"
    )


# =========================================================
# VERIFY CHANNEL JOIN
# =========================================================

async def verify_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    is_member = await is_channel_member(
        context.bot,
        user_id
    )

    if not is_member:

        await query.answer(
            "❌ You have not joined the channel yet.",
            show_alert=True
        )

        return

    await query.edit_message_text(
        (
            "✅ <b>Verification Successful!</b>\n\n"
            "You can now use TaskMint."
        ),
        parse_mode="HTML"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🏠 <b>Main Menu</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        (
            "🎯 <b>Tasks</b>\n\n"
            "No tasks are available right now.\n\n"
            "New tasks will appear here when "
            "the admin adds them."
        ),
        parse_mode="HTML"
    )


# =========================================================
# REFERRAL
# =========================================================

async def refer_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    user = get_user(user_id)

    referrals = 0

    if user:

        referrals = user.get(
            "referrals",
            0
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
            f"👤 Total Referrals: <b>{referrals}</b>\n\n"
            "Invite your friends using your "
            "personal referral link.\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>"
        ),
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW MENU
# =========================================================

async def withdraw_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    balance = get_balance(
        user_id
    )

    context.user_data.clear()

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: "
                f"<b>{balance:.6f} POL</b>\n"
                f"📌 Minimum Withdrawal: "
                f"<b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
                "❌ Your balance is not enough "
                "for withdrawal."
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
            f"💰 Available Balance: "
            f"<b>{balance:.6f} POL</b>\n"
            f"📌 Minimum Withdrawal: "
            f"<b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
            "Please enter the amount of "
            "<b>POL</b> you want to withdraw."
        ),
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW PROCESS
# =========================================================

async def process_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    step = context.user_data.get(
        "withdraw_step"
    )

    # STEP 1: AMOUNT
    if step == "amount":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Please enter a valid POL amount."
            )

            return

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount must be greater than 0."
            )

            return

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                (
                    f"❌ Minimum withdrawal is "
                    f"{MIN_WITHDRAW:.6f} POL."
                )
            )

            return

        balance = get_balance(
            user_id
        )

        if amount > balance:

            await update.message.reply_text(
                "❌ Insufficient POL balance."
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
                "👛 <b>POL Wallet Address</b>\n\n"
                "Please send your POL wallet address."
            ),
            parse_mode="HTML"
        )

        return

    # STEP 2: WALLET
    if step == "wallet":

        wallet = text

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if not amount:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Withdrawal session expired. "
                "Please try again."
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
                "❌ Insufficient balance or "
                "withdrawal could not be processed."
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

        except PyMongoError as error:

            print(
                "Withdrawal database error:",
                error
            )

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
                "❌ Withdrawal could not be saved. "
                "Your balance has not been lost. "
                "Please try again."
            )

            return

        withdrawal_id = str(
            result.inserted_id
        )

        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Withdrawal Submitted</b>\n\n"
                f"🆔 ID: <code>{withdrawal_id}</code>\n"
                f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                f"👛 Wallet: <code>{wallet}</code>\n"
                "📌 Status: <b>Pending</b>\n\n"
                "Your withdrawal request has been "
                "sent to the admin."
            ),
            parse_mode="HTML"
        )

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 <b>New Withdrawal Request</b>\n\n"
                    f"🆔 ID: <code>{withdrawal_id}</code>\n"
                    f"👤 User ID: <code>{user_id}</code>\n"
                    f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                    f"👛 Wallet: <code>{wallet}</code>\n"
                    "📌 Status: <b>Pending</b>"
                ),
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "Admin notification error:",
                error
            )

        return
