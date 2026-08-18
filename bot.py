import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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
# /start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 বন্ধুদের Refer করে Points earn করতে পারবে।\n"
        "🎁 Daily Bonus পেতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# /help
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন\n\n"
        "কোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করুন।"
    )


# =========================
# Menu Button Handler
# =========================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    text = update.message.text

    # Earn Tasks
    if text == "💰 Earn Tasks":

        await update.message.reply_text(
            "💰 Earn Tasks\n\n"
            "📌 বর্তমানে কোনো Task available নেই।\n"
            "শীঘ্রই নতুন Tasks যোগ করা হবে!"
        )

    # Referral
    elif text == "👥 Refer & Earn":

        user_id = update.effective_user.id

        await update.message.reply_text(
            "👥 Refer & Earn\n\n"
            "তোমার Referral Link:\n"
            f"https://t.me/TaskMintBot?start={user_id}\n\n"
            "বন্ধুদের এই link দিয়ে invite করো।\n"
            "Referral system শীঘ্রই fully চালু হবে।"
        )

    # Withdraw
    elif text == "💳 Withdraw":

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            "তোমার বর্তমান Balance: 0 Points\n\n"
            "⚠️ Withdrawal করতে হলে minimum points লাগবে।\n"
            "Withdrawal system শীঘ্রই চালু হবে।"
        )

    # Daily Bonus
    elif text == "🎁 Daily Bonus":

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "আজকের Bonus: 0 Points\n\n"
            "Daily Bonus system শীঘ্রই চালু হবে।"
        )

    # Balance
    elif text == "📊 My Balance":

        await update.message.reply_text(
            "📊 My Balance\n\n"
            "💰 Points: 0\n"
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
            "Please add BOT_TOKEN in Render Environment Variables."
        )

    # Start Render health server
    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    # Create Telegram application
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    # Buttons / Text
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            button_handler
        )
    )

    print("TaskMint Bot is running...")

    # Start bot
    app.run_polling()


# =========================
# Run
# =========================

if __name__ == "__main__":
    main()
