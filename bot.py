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
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))


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
# User Points
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
# Start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in user_points:
        user_points[user_id] = 0

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 বন্ধুদের Refer করে Points earn করতে পারবে।\n"
        "🎁 Daily Bonus পেতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# Help
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন"
    )


# =========================
# Earn Tasks
# =========================

async def earn_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Telegram Channel (+10 Points)",
                callback_data="task_join"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back to Menu",
                callback_data="back_menu"
            )
        ]
    ]

    await update.message.reply_text(
        "💰 Earn Tasks\n\n"
        "নিচের Task complete করে Points earn করো 👇\n\n"
        "📢 Join Telegram Channel\n"
        "💰 Reward: 10 Points",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# Task Callback
# =========================

async def task_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # Join task
    if query.data == "task_join":

        keyboard = [
            [
                InlineKeyboardButton(
                    "📢 Join Channel",
                    url="https://t.me/TaskMintBot"
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Check Task",
                    callback_data="check_join"
                )
            ]
        ]

        await query.edit_message_text(
            "📢 Join Telegram Channel\n\n"
            "1️⃣ নিচের Join Channel button-এ চাপ দাও।\n"
            "2️⃣ Channel-এ Join করো।\n"
            "3️⃣ তারপর নিচের ✅ Check Task button চাপো।\n\n"
            "💰 Reward: 10 Points",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # Check task
    elif query.data == "check_join":

        # Already completed
        completed_tasks = context.user_data.setdefault(
            "completed_tasks",
            set()
        )

        if "join_task" in completed_tasks:

            await query.edit_message_text(
                "⚠️ তুমি এই Task আগেই complete করেছো!\n\n"
                "এই Task থেকে আর Points পাওয়া যাবে না।"
            )

            return

        # Give reward
        user_points[user_id] = user_points.get(user_id, 0) + 10

        completed_tasks.add("join_task")

        await query.edit_message_text(
            "🎉 Task Completed!\n\n"
            "✅ তুমি পেয়েছো +10 Points\n\n"
            f"💰 Total Points: {user_points[user_id]}"
        )

    # Back
    elif query.data == "back_menu":

        await query.message.delete()

        await query.message.reply_text(
            "🏠 Main Menu\n\n"
            "নিচের Menu থেকে একটি option বেছে নাও 👇",
            reply_markup=reply_markup
        )


# =========================
# Button Handler
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text

    # Earn Tasks
    if text == "💰 Earn Tasks":

        await earn_tasks(update, context)

    # Refer
    elif text == "👥 Refer & Earn":

        user_id = update.effective_user.id

        await update.message.reply_text(
            "👥 Refer & Earn\n\n"
            "তোমার Referral Link:\n"
            f"https://t.me/TaskMintBot?start={user_id}\n\n"
            "Referral system শীঘ্রই চালু হবে।"
        )

    # Withdraw
    elif text == "💳 Withdraw":

        points = user_points.get(
            update.effective_user.id,
            0
        )

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            f"💰 Your Points: {points}\n\n"
            "Withdrawal system শীঘ্রই চালু হবে।"
        )

    # Daily Bonus
    elif text == "🎁 Daily Bonus":

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "Daily Bonus system শীঘ্রই চালু হবে।"
        )

    # Balance
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

    # Help
    elif text == "ℹ️ Help":

        await help_command(update, context)


# =========================
# Main
# =========================

def main():

    if not TOKEN:
        raise ValueError(
            "BOT_TOKEN is not set. "
            "Add BOT_TOKEN in Render Environment Variables."
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

    app.add_handler(
        CallbackQueryHandler(task_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            button_handler
        )
    )

    print("TaskMint Bot is running...")

    app.run_polling()


# =========================
# Run
# =========================

if __name__ == "__main__":
    main()
