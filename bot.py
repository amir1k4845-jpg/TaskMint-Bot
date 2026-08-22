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
CHANNEL_LINK = "https://t.me/TaskMint_v1"

TOKEN_NAME = "POL"

MIN_WITHDRAW = 1.0

DATABASE_NAME = "taskmint"


# =========================================================
# CONFIG CHECK
# =========================================================

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

db = mongo_client[DATABASE_NAME]

users_collection = db["users"]

withdrawals_collection = db["withdrawals"]

tasks_collection = db["tasks"]


# =========================================================
# DATABASE SETUP
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

    print("MongoDB database ready.")


# =========================================================
# USER FUNCTIONS
# =========================================================

def create_or_update_user(user):

    now = datetime.now(timezone.utc)

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
                "referred_by": None,
                "completed_tasks": [],
                "created_at": now
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
        user.get("balance", 0.0)
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
# CHANNEL JOIN KEYBOARD
# =========================================================

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
                    "✅ Verify Join",
                    callback_data="verify_join"
                )
            ]
        ]
    )


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

    create_or_update_user(user)

    # IMPORTANT:
    # Do NOT show menu before channel verification.

    is_member = await is_channel_member(
        context.bot,
        user.id
    )

    if not is_member:

        await update.message.reply_text(
            (
                "🔐 <b>Join Required</b>\n\n"
                "Welcome to TaskMint!\n\n"
                "To continue, you must join "
                "our official channel first.\n\n"
                "After joining, click "
                "<b>Verify Join</b>."
            ),
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )

        return

    # Only verified users reach here.

    await show_main_menu(
        update,
        user.id
    )


# =========================================================
# MAIN MENU
# =========================================================

async def show_main_menu(
    update,
    user_id
):

    await update.message.reply_text(
        (
            "🏠 <b>TaskMint Main Menu</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


# =========================================================
# VERIFY JOIN
# =========================================================

async def verify_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user_id = query.from_user.id

    is_member = await is_channel_member(
        context.bot,
        user_id
    )

    if not is_member:

        await query.answer(
            "❌ Please join the channel first.",
            show_alert=True
        )

        return

    await query.answer(
        "✅ Join verified!"
    )

    await query.edit_message_text(
        (
            "✅ <b>Verification Successful!</b>\n\n"
            "Welcome to TaskMint."
        ),
        parse_mode="HTML"
    )

    # Send menu ONLY after successful verification.

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🏠 <b>TaskMint Main Menu</b>\n\n"
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
            "Tasks will be added from "
            "the Admin Panel."
        ),
        parse_mode="HTML"
    )


# =========================================================
# REFER
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
            f"👤 Total Referrals: "
            f"<b>{referrals}</b>\n\n"
            "Invite your friends using "
            "your referral link.\n\n"
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

    balance = get_balance(user_id)

    context.user_data.clear()

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: "
                f"<b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: "
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

    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    if step == "amount":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Enter a valid POL amount."
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

        balance = get_balance(user_id)

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

    # -----------------------------------------------------
    # WALLET
    # -----------------------------------------------------

    if step == "wallet":

        wallet = text

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if not amount:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Withdrawal session expired."
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

        withdrawal = {
            "user_id": user_id,
            "amount": amount,
            "token": TOKEN_NAME,
            "wallet": wallet,
            "status": "pending",
            "created_at": datetime.now(
                timezone.utc
            ),
            "updated_at": datetime.now(
                timezone.utc
            )
        }

        try:

            result = (
                withdrawals_collection.insert_one(
                    withdrawal
                )
            )

        except PyMongoError as error:

            print(
                "Withdrawal error:",
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
                "❌ Withdrawal failed. "
                "Your balance has been restored."
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
                f"💰 Amount: "
                f"<b>{amount:.6f} POL</b>\n"
                f"👛 Wallet: "
                f"<code>{wallet}</code>\n"
                "📌 Status: <b>Pending</b>"
            ),
            parse_mode="HTML"
        )

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 <b>New Withdrawal</b>\n\n"
                    f"🆔 ID: "
                    f"<code>{withdrawal_id}</code>\n"
                    f"👤 User ID: "
                    f"<code>{user_id}</code>\n"
                    f"💰 Amount: "
                    f"<b>{amount:.6f} POL</b>\n"
                    f"👛 Wallet: "
                    f"<code>{wallet}</code>\n"
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
# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if user_id != ADMIN_ID:

        await update.message.reply_text(
            "❌ You are not authorized."
        )

        return

    total_users = users_collection.count_documents({})

    total_tasks = tasks_collection.count_documents({})

    pending_withdrawals = (
        withdrawals_collection.count_documents(
            {
                "status": "pending"
            }
        )
    )

    await update.message.reply_text(
        (
            "👑 <b>Admin Panel</b>\n\n"
            f"👥 Total Users: "
            f"<b>{total_users}</b>\n"
            f"🎯 Total Tasks: "
            f"<b>{total_tasks}</b>\n"
            f"💳 Pending Withdrawals: "
            f"<b>{pending_withdrawals}</b>\n\n"
            "⚙️ More admin features "
            "will be added next."
        ),
        parse_mode="HTML"
    )


# =========================================================
# MENU HANDLER
# =========================================================

async def menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text

    # -----------------------------------------------------
    # SECURITY:
    # USER MUST BE IN CHANNEL
    # -----------------------------------------------------

    is_member = await is_channel_member(
        context.bot,
        user_id
    )

    if not is_member:

        await send_join_message(
            update
        )

        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if text == "🎯 Tasks":

        await tasks_menu(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # WITHDRAW
    # -----------------------------------------------------

    if text == "💳 Withdraw":

        await withdraw_menu(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # REFER
    # -----------------------------------------------------

    if text == "👥 Refer":

        await refer_menu(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # ADMIN PANEL
    # -----------------------------------------------------

    if text == "👑 Admin Panel":

        await admin_panel(
            update,
            context
        )

        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Withdrawal conversation
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


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        repr(context.error)
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # -----------------------------------------------------
    # RENDER HEALTH SERVER
    # -----------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # -----------------------------------------------------
    # MONGODB CONNECTION
    # -----------------------------------------------------

    print(
        "Connecting to MongoDB..."
    )

    try:

        mongo_client.admin.command(
            "ping"
        )

        print(
            "MongoDB connected successfully."
        )

    except Exception as error:

        print(
            "MongoDB connection failed:",
            error
        )

        raise

    setup_database()

    # -----------------------------------------------------
    # TELEGRAM BOT
    # -----------------------------------------------------

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # Verify Join
    application.add_handler(
        CallbackQueryHandler(
            verify_join,
            pattern="^verify_join$"
        )
    )

    # Text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    # Error handler
    application.add_error_handler(
        error_handler
    )

    print(
        "TaskMint Bot is running..."
    )

    # -----------------------------------------------------
    # START POLLING
    # -----------------------------------------------------

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
