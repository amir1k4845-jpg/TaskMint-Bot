import os
import sqlite3
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

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")

PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = 7003609983

CHANNEL_USERNAME = "@Amir10m300"

CHANNEL_LINK = "https://t.me/Amir10m300"

DB_FILE = "taskmint.db"


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            points INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            points INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def add_user(user_id, username, first_name):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cursor.fetchone()

    if not exists:
        cursor.execute(
            """
            INSERT INTO users
            (user_id, username, first_name, points)
            VALUES (?, ?, ?, 0)
            """,
            (user_id, username, first_name)
        )

    else:
        cursor.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (username, first_name, user_id)
        )

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    user = cursor.fetchone()

    conn.close()

    return user


def get_points(user_id):
    user = get_user(user_id)

    if user:
        return user["points"]

    return 0


def add_points(user_id, amount):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id = ?
        """,
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def remove_points(user_id, amount):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET points = points - ?
        WHERE user_id = ?
        AND points >= ?
        """,
        (amount, user_id, amount)
    )

    changed = cursor.rowcount

    conn.commit()
    conn.close()

    return changed > 0


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        return


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# =========================
# KEYBOARDS
# =========================

def main_menu():
    keyboard = [
        ["💰 Earn Tasks", "👥 Refer & Earn"],
        ["🎁 Daily Bonus", "💳 My Balance"],
        ["💸 Withdraw", "📊 Statistics"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


def back_menu():
    keyboard = [
        ["🔙 Back to Menu"]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    add_user(
        user.id,
        user.username,
        user.first_name
    )

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🚀 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn points.\n"
        "👥 Invite friends and earn rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💸 Withdraw your earnings.\n\n"
        "👇 Choose an option:",
        reply_markup=main_menu()
    )


# =========================
# BASIC COMMAND
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "💰 Earn points by completing tasks.\n"
        "👥 Invite friends to earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Check your balance.\n"
        "💸 Withdraw your points.",
        reply_markup=main_menu()
    )


# =========================
# INIT DATABASE
# =========================

init_db()
