import os
from datetime import datetime, timezone

from pymongo import MongoClient
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
    MessageHandler,
    ContextTypes,
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


# =========================================================
# CHECK CONFIG
# =========================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing.")

if not MONGO_URI:
    raise ValueError("MONGO_URI is missing.")


# =========================================================
# MONGODB
# =========================================================

mongo_client = MongoClient(MONGO_URI)

database = mongo_client["taskmint"]

users_collection = database["users"]
withdrawals_collection = database["withdrawals"]
tasks_collection = database["tasks"]


# =========================================================
# DATABASE INDEXES
# =========================================================

users_collection.create_index(
    "user_id",
    unique=True
)

withdrawals_collection.create_index(
    "user_id"
)

tasks_collection.create_index(
    "task_id",
    unique=True
)


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
        user.get("balance", 0.0)
    )


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(user_id):

    buttons = [
        ["🎯 Tasks"],
        ["💳 Withdraw", "👥 Refer"],
    ]

    if user_id == ADMIN_ID:
        buttons.append(
            ["👑 Admin Panel"]
        )

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True
    )


# =========================================================
# REQUIRED CHANNEL
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


async def check_channel_membership(
    bot,
    user_id
):

    try:

        member = await bot.get_chat_member(
            REQUIRED_CHANNEL,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator",
        )

    except Exception as error:

        print(
            "Channel membership error:",
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

    is_member = await check_channel_membership(
        context.bot,
        user.id
    )

    if not is_member:

        await update.message.reply_text(
            (
                "🔐 <b>Join Required</b>\n\n"
                "To use TaskMint, you must join "
                "our official channel first.\n\n"
                "1️⃣ Join the channel\n"
                "2️⃣ Click Verify Join\n"
                "3️⃣ Access your dashboard"
            ),
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )

        return

    await send_main_menu(
        update,
        user.id
    )


# =========================================================
# MAIN MENU MESSAGE
# =========================================================

async def send_main_menu(
    update,
    user_id
):

    await update.message.reply_text(
        (
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option from the menu below."
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

    await query.answer()

    user_id = query.from_user.id

    is_member = await check_channel_membership(
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
        f"https://t.me/{bot_info.username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{referrals}</b>\n\n"
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

    balance = get_balance(user_id)

    context.user_data.clear()

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: <b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: <b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
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
                "❌ Please enter a valid POL amount."
            )

            return

        balance = get_balance(user_id)

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                (
                    f"❌ Minimum withdrawal is "
                    f"{MIN_WITHDRAW:.6f} POL."
                )
            )

            return

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
                "💳 <b>Wallet Address</b>\n\n"
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

        balance = get_balance(user_id)

        if amount > balance:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Insufficient balance."
            )

            return

        now = datetime.now(timezone.utc)

        # Reserve/deduct balance
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
                "❌ Unable to process withdrawal."
            )

            return

        withdrawal = {
            "user_id": user_id,
            "amount": amount,
            "token": TOKEN_NAME,
            "wallet": wallet,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }

        result = withdrawals_collection.insert_one(
            withdrawal
        )

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

        # Notify admin
        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 <b>New Withdrawal</b>\n\n"
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

    pending_withdrawals = withdrawals_collection.count_documents(
        {
            "status": "pending"
        }
    )

    await update.message.reply_text(
        (
            "👑 <b>Admin Panel</b>\n\n"
            f"👥 Total Users: <b>{total_users}</b>\n"
            f"💳 Pending Withdrawals: "
            f"<b>{pending_withdrawals}</b>\n\n"
            "⚙️ Advanced admin controls "
            "will be added next."
        ),
        parse_mode="HTML"
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text

    user_id = update.effective_user.id

    # =====================================================
    # SECURITY CHECK
    # =====================================================

    is_member = await check_channel_membership(
        context.bot,
        user_id
    )

    if not is_member:

        await update.message.reply_text(
            (
                "🔐 <b>Channel Join Required</b>\n\n"
                "Please join the required channel "
                "before using the bot."
            ),
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # TASKS
    # =====================================================

    if text == "🎯 Tasks":

        await tasks_menu(
            update,
            context
        )

        return

    # =====================================================
    # WITHDRAW
    # =====================================================

    if text == "💳 Withdraw":

        await withdraw_menu(
            update,
            context
        )

        return

    # =====================================================
    # REFER
    # =====================================================

    if text == "👥 Refer":

        await refer_menu(
            update,
            context
        )

        return

    # =====================================================
    # ADMIN
    # =====================================================

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

    if context.user_data.get(
        "withdraw_step"
    ):

        await process_withdraw(
            update,
            context
        )

        return

    await button_handler(
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
        context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "Connecting to MongoDB..."
    )

    mongo_client.admin.command(
        "ping"
    )

    print(
        "MongoDB connected successfully."
    )

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
            verify_join,
            pattern="^verify_join$"
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
        "TaskMint Bot is running..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
