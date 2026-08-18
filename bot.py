import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

CHANNEL_USERNAME = "@Amir10m300"
TASK_REWARD = 10

ADMIN_ID = 7003609983
MIN_WITHDRAW = 100

AMOUNT, METHOD, ACCOUNT, CONFIRM = range(4)


# =========================
# Render Health Server
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# =========================
# User Data
# =========================

user_points = {}


# =========================
# Main Menu
# =========================

keyboard = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in user_points:
        user_points[user_id] = 0

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 বন্ধুদের Refer করে Points earn করতে পারবে।\n"
        "💳 Points দিয়ে Withdrawal request করতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# HELP
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন\n\n"
        "সমস্যা হলে Admin-এর সাথে যোগাযোগ করুন।"
    )


# =========================
# EARN TASKS
# =========================

async def earn_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url="https://t.me/Amir10m300"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Check Task",
                callback_data="check_join"
            )
        ]
    ]

    await update.message.reply_text(
        "💰 Earn Tasks\n\n"
        "📢 Join our Telegram Channel\n\n"
        f"💰 Reward: +{TASK_REWARD} Points\n\n"
        "1️⃣ Join Channel button চাপো\n"
        "2️⃣ Channel-এ Join করো\n"
        "3️⃣ তারপর ✅ Check Task চাপো",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# WITHDRAW START
# =========================

async def withdraw_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    points = user_points.get(user_id, 0)

    if points < MIN_WITHDRAW:

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            f"💰 Your Points: {points}\n"
            f"⚠️ Minimum Withdrawal: {MIN_WITHDRAW} Points\n\n"
            "আরও Points earn করে আবার চেষ্টা করো।"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 Withdrawal\n\n"
        f"💰 Available Points: {points}\n\n"
        "কত Points withdraw করতে চাও?\n\n"
        f"Minimum: {MIN_WITHDRAW} Points\n\n"
        "শুধু সংখ্যা লিখো।"
    )

    return AMOUNT


# =========================
# WITHDRAW AMOUNT
# =========================

async def withdraw_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    points = user_points.get(user_id, 0)

    try:
        amount = int(update.message.text.strip())
    except ValueError:

        await update.message.reply_text(
            "❌ সঠিক সংখ্যা লিখো।\n\n"
            "উদাহরণ: 100"
        )

        return AMOUNT

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            f"❌ Minimum Withdrawal {MIN_WITHDRAW} Points।"
        )

        return AMOUNT

    if amount > points:

        await update.message.reply_text(
            "❌ তোমার কাছে এত Points নেই!\n\n"
            f"💰 Available: {points} Points"
        )

        return AMOUNT

    context.user_data["withdraw_amount"] = amount

    keyboard = [
        ["💰 Binance"],
        ["📱 bKash"],
        ["📱 Nagad"],
    ]

    await update.message.reply_text(
        "💳 Payment Method নির্বাচন করো:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return METHOD


# =========================
# WITHDRAW METHOD
# =========================

async def withdraw_method(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    method = update.message.text.strip()

    allowed_methods = [
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad",
    ]

    if method not in allowed_methods:

        await update.message.reply_text(
            "❌ একটি valid payment method নির্বাচন করো।"
        )

        return METHOD

    context.user_data["withdraw_method"] = method

    await update.message.reply_text(
        f"✅ Payment Method: {method}\n\n"
        "এখন তোমার payment account number / ID পাঠাও।"
    )

    return ACCOUNT


# =========================
# WITHDRAW ACCOUNT
# =========================

async def withdraw_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    account = update.message.text.strip()

    if len(account) < 4:

        await update.message.reply_text(
            "❌ সঠিক account number / ID দাও।"
        )

        return ACCOUNT

    context.user_data["withdraw_account"] = account

    amount = context.user_data["withdraw_amount"]
    method = context.user_data["withdraw_method"]

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Confirm",
                callback_data="withdraw_confirm"
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="withdraw_cancel"
            )
        ]
    ]

    await update.message.reply_text(
        "🔎 Withdrawal Details\n\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n\n"
        "সব ঠিক থাকলে Confirm চাপো।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return CONFIRM


# =========================
# WITHDRAW CONFIRM
# =========================

async def withdraw_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id
    username = query.from_user.username

    amount = context.user_data.get("withdraw_amount")
    method = context.user_data.get("withdraw_method")
    account = context.user_data.get("withdraw_account")

    if query.data == "withdraw_cancel":

        context.user_data.clear()

        await query.edit_message_text(
            "❌ Withdrawal cancelled."
        )

        return ConversationHandler.END

    if query.data == "withdraw_confirm":

        points = user_points.get(user_id, 0)

        if amount is None or amount > points:

            await query.edit_message_text(
                "❌ Withdrawal failed.\n\n"
                "তোমার balance পরিবর্তিত হয়েছে।"
            )

            context.user_data.clear()

            return ConversationHandler.END

        # Reserve / deduct points
        user_points[user_id] = points - amount

        admin_text = (
            "🔔 NEW WITHDRAWAL REQUEST\n\n"
            f"👤 User ID: {user_id}\n"
            f"👤 Username: @{username if username else 'No Username'}\n"
            f"💰 Amount: {amount} Points\n"
            f"💳 Method: {method}\n"
            f"📱 Account: {account}\n"
            f"💵 Remaining Points: {user_points[user_id]}"
        )

        admin_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve_{user_id}_{amount}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_{user_id}_{amount}"
                )
            ]
        ])

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_text,
                reply_markup=admin_keyboard
            )

        except Exception as e:

            print("Admin notification error:", e)

        await query.edit_message_text(
            "✅ Withdrawal Request Submitted!\n\n"
            f"💰 Amount: {amount} Points\n"
            f"💳 Method: {method}\n"
            f"📱 Account: {account}\n\n"
            "⏳ Admin verification-এর জন্য অপেক্ষা করো।"
        )

        context.user_data.clear()

        return ConversationHandler.END


# =========================
# ADMIN APPROVE / REJECT
# =========================

async def admin_withdraw_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ You are not authorized.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    parts = data.split("_")

    if len(parts) != 3:
        return

    action = parts[0]
    user_id = int(parts[1])
    amount = int(parts[2])

    if action == "approve":

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            "✅ STATUS: APPROVED"
        )

        try:

            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ Withdrawal Approved!\n\n"
                    f"💰 Amount: {amount} Points\n\n"
                    "Admin তোমার withdrawal approve করেছে।"
                )
            )

        except Exception as e:

            print("User notification error:", e)

    elif action == "reject":

        user_points[user_id] = (
            user_points.get(user_id, 0)
            + amount
        )

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            "❌ STATUS: REJECTED\n"
            f"↩️ {amount} Points returned."
        )

        try:

            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ Withdrawal Rejected\n\n"
                    f"↩️ {amount} Points তোমার balance-এ ফেরত দেওয়া হয়েছে।"
                )
            )

        except Exception as e:

            print("User notification error:", e)


# =========================
# TASK CALLBACK
# =========================

async def task_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if query.data == "check_join":

        try:

            member = await context.bot.get_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=user_id
            )

            status = member.status

            if status in [
                "member",
                "administrator",
                "creator"
            ]:

                completed_tasks = context.user_data.setdefault(
                    "completed_tasks",
                    set()
                )

                if "join_task" in completed_tasks:

                    await query.edit_message_text(
                        "⚠️ এই Task তুমি আগেই complete করেছো!\n\n"
                        f"💰 Current Points: "
                        f"{user_points.get(user_id, 0)}"
                    )

                    return

                user_points[user_id] = (
                    user_points.get(user_id, 0)
                    + TASK_REWARD
                )

                completed_tasks.add("join_task")

                await query.edit_message_text(
                    "🎉 Task Completed!\n\n"
                    f"✅ +{TASK_REWARD} Points Added!\n\n"
                    f"💰 Total Points: "
                    f"{user_points[user_id]}"
                )

            else:

                await query.edit_message_text(
                    "❌ তুমি এখনো Channel-এ Join করোনি!\n\n"
                    "প্রথমে Channel-এ Join করো।",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(
                                "📢 Join Channel",
                                url="https://t.me/Amir10m300"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🔄 Check Again",
                                callback_data="check_join"
                            )
                        ]
                    ])
                )

        except Exception as e:

            print("Channel check error:", e)

            await query.edit_message_text(
                "⚠️ Channel verification করা যাচ্ছে না।\n\n"
                "নিশ্চিত করো Bot-কে Channel-এর Admin করা হয়েছে।"
            )


# =========================
# BUTTON HANDLER
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text

    if text == "💰 Earn Tasks":

        await earn_tasks(update, context)

    elif text == "👥 Refer & Earn":

        user_id = update.effective_user.id

        await update.message.reply_text(
            "👥 Refer & Earn\n\n"
            f"🔗 Referral Link:\n"
            f"https://t.me/TaskMintBot?start={user_id}\n\n"
            "Referral system শীঘ্রই চালু হবে।"
        )

    elif text == "💳 Withdraw":

        await withdraw_start(update, context)

    elif text == "🎁 Daily Bonus":

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "Daily Bonus system শীঘ্রই চালু হবে।"
        )

    elif text == "📊 My Balance":

        points = user_points.get(
            update.effective_user.id,
            0
        )

        await update.message.reply_text(
            "📊 My Balance\n\n"
            f"💰 Points: {points}\n"
            "💵 Balance: $0.00"
        )

    elif text == "ℹ️ Help":

        await help_command(update, context)


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:
        raise ValueError(
            "BOT_TOKEN is not set."
        )

    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    # Withdrawal conversation
    withdraw_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^💳 Withdraw$"),
                withdraw_start
            )
        ],
        states={
            AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_amount
                )
            ],
            METHOD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_method
                )
            ],
            ACCOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_account
                )
            ],
            CONFIRM: [
                CallbackQueryHandler(
                    withdraw_confirm,
                    pattern="^withdraw_(confirm|cancel)$"
                )
            ],
        },
        fallbacks=[],
    )

    app.add_handler(withdraw_conversation)

    # Admin approve/reject
    app.add_handler(
        CallbackQueryHandler(
            admin_withdraw_callback,
            pattern="^(approve|reject)_"
        )
    )

    # Task callbacks
    app.add_handler(
        CallbackQueryHandler(
            task_callback,
            pattern="^check_join$"
        )
    )

    # Other menu buttons
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            button_handler
        )
    )

    print("TaskMint Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
