import os
import sqlite3
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
DB_FILE = "taskmint.db"


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
# Database
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            task_id TEXT,
            PRIMARY KEY (user_id, task_id)
        )
    """)

    conn.commit()
    conn.close()


def add_user(user_id):
    conn = sqlite3.connect(DB_FILE)

    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, points) VALUES (?, 0)",
        (user_id,)
    )

    conn.commit()
    conn.close()


def get_points(user_id):
    add_user(user_id)

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.execute(
        "SELECT points FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else 0


def add_points(user_id, amount):
    add_user(user_id)

    conn = sqlite3.connect(DB_FILE)

    conn.execute(
        "UPDATE users SET points = points + ? WHERE user_id = ?",
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def task_completed(user_id, task_id):
    conn = sqlite3.connect(DB_FILE)

    cursor = conn.execute(
        "SELECT 1 FROM completed_tasks WHERE user_id = ? AND task_id = ?",
        (user_id, task_id)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def complete_task(user_id, task_id, reward):
    if task_completed(user_id, task_id):
        return False

    conn = sqlite3.connect(DB_FILE)

    conn.execute(
        "INSERT INTO completed_tasks (user_id, task_id) VALUES (?, ?)",
        (user_id, task_id)
    )

    conn.execute(
        "UPDATE users SET points = points + ? WHERE user_id = ?",
        (reward, user_id)
    )

    conn.commit()
    conn.close()

    return True


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

    add_user(user_id)

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করো।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# Help
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "💰 Earn Tasks - Tasks করে Points earn\n"
        "📊 My Balance - তোমার Points দেখো\n"
        "👥 Refer & Earn - Referral system\n"
        "💳 Withdraw - Points withdraw\n"
        "🎁 Daily Bonus - Daily bonus"
    )


# =========================
# Button Handler
# =========================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    user_id = update.effective_user.id

    add_user(user_id)

    # Earn Tasks
    if text == "💰 Earn Tasks":

        await update.message.reply_text(
            "💰 Available Tasks\n\n"
            "━━━━━━━━━━━━━━\n"
            "🎯 Task 1\n"
            "📌 Task: Visit our page\n"
            "💎 Reward: 10 Points\n\n"
            "Task complete করার পর নিচের command ব্যবহার করো:\n"
            "/task1"
        )

    # Referral
    elif text == "👥 Refer & Earn":

        await update.message.reply_text(
            "👥 Refer & Earn\n\n"
            "Referral system পরের ধাপে যোগ করা হবে।"
        )

    # Withdraw
    elif text == "💳 Withdraw":

        points = get_points(user_id)

        await update.message.reply_text(
            f"💳 Withdraw\n\n"
            f"💰 Your Points: {points}\n\n"
            "Withdrawal system পরের ধাপে যোগ করা হবে।"
        )

    # Daily Bonus
    elif text == "🎁 Daily Bonus":

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "Daily bonus system পরের ধাপে যোগ করা হবে।"
        )

    # Balance
    elif text == "📊 My Balance":

        points = get_points(user_id)

        await update.message.reply_text(
            f"📊 My Balance\n\n"
            f"💰 Points: {points}"
        )

    # Help
    elif text == "ℹ️ Help":

        await help_command(update, context)


# =========================
# Task 1
# =========================

async def task1(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    success = complete_task(
        user_id,
        "task1",
        10
    )

    if success:

        points = get_points(user_id)

        await update.message.reply_text(
            "✅ Task Completed!\n\n"
            "🎉 You received +10 Points.\n\n"
            f"💰 Total Points: {points}"
        )

    else:

        await update.message.reply_text(
            "⚠️ তুমি এই task আগেই complete করেছো।"
        )


# =========================
# Main
# =========================

def main():

    if not TOKEN:
        raise ValueError("BOT_TOKEN is not set")

    init_db()

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
        CommandHandler("task1", task1)
    )

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
