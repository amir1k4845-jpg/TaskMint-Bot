import os
import threading
import sqlite3
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

ADMIN_ID = 7003609983

CHANNEL_USERNAME = "@Amir10m300"
CHANNEL_URL = "https://t.me/Amir10m300"

TASK_REWARD = 10
MIN_WITHDRAW = 100

DB_FILE = "taskmint.db"

AMOUNT, METHOD, ACCOUNT = range(3)


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    conn.commit()
    conn.close()


def register_user(user):

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, first_name, points)
        VALUES (?, ?, ?, 0)
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
    ))

    conn.execute("""
        UPDATE users
        SET username = ?, first_name = ?
        WHERE user_id = ?
    """, (
        user.username or "",
        user.first_name or "",
        user.id,
    ))

    conn.commit()
    conn.close()


def get_points(user_id):

    conn = get_db()

    row = conn.execute(
        "SELECT points FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    if row:
        return row["points"]

    return 0


def add_points(user_id, amount):

    conn = get_db()

    conn.execute(
        "UPDATE users SET points = points + ? WHERE user_id = ?",
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def remove_points(user_id, amount):

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET points = MAX(points - ?, 0)
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


def task_completed(user_id, task_id):

    conn = get_db()

    row = conn.execute("""
        SELECT 1
        FROM completed_tasks
        WHERE user_id = ? AND task_id = ?
    """, (
        user_id,
        task_id
    )).fetchone()

    conn.close()

    return row is not None


def mark_task_completed(user_id, task_id):

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO completed_tasks
        (user_id, task_id)
        VALUES (?, ?)
    """, (
        user_id,
        task_id
    ))

    conn.commit()
    conn.close()


# =========================
# RENDER SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"TaskMint Bot is running!"
        )

    def log_message(self, format, *args):
        pass


def start_web_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    server.serve_forever()


# =========================
# MAIN MENU
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

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 Refer করে Points earn করতে পারবে।\n"
        "💳 Points দিয়ে Withdraw করতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# HELP
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন\n\n"
        "সমস্যা হলে Admin-এর সাথে যোগাযোগ করুন।"
    )


# =========================
# EARN TASKS
# =========================

async def earn_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_URL
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
        "1️⃣ Join Channel চাপো\n"
        "2️⃣ Channel-এ Join করো\n"
        "3️⃣ তারপর ✅ Check Task চাপো",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )
