import os
import sqlite3
import threading
import re

from datetime import datetime
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


# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = 7003609983

DB_FILE = "taskmint.db"

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100


# =========================
# WITHDRAW STATES
# =========================

AMOUNT, METHOD, ACCOUNT = range(3)


# =========================
# DEFAULT BUTTONS
# =========================

MENU = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]


DEFAULT_SETTINGS = {
    "button_earn": "💰 Earn Tasks",
    "button_referral": "👥 Refer & Earn",
    "button_withdraw": "💳 Withdraw",
    "button_daily": "🎁 Daily Bonus",
    "button_balance": "📊 My Balance",
    "button_help": "ℹ️ Help",

    "feature_earn": "1",
    "feature_referral": "1",
    "feature_withdraw": "1",
    "feature_daily": "1",
    "feature_balance": "1",
    "feature_help": "1",

    "reward_task": "10",
    "reward_referral": "20",
    "reward_daily": "10",
    "min_withdraw": "100",
}


BUTTON_KEYS = [
    ("earn", "Earn Tasks"),
    ("referral", "Refer & Earn"),
    ("withdraw", "Withdraw"),
    ("daily", "Daily Bonus"),
    ("balance", "My Balance"),
    ("help", "Help"),
]


# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            points INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            referral_rewarded INTEGER DEFAULT 0,
            last_bonus TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_key TEXT,
            UNIQUE(user_id, task_key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            channel TEXT,
            channel_url TEXT,
            reward INTEGER DEFAULT 10,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    for key, value in DEFAULT_SETTINGS.items():

        conn.execute(
            """
            INSERT OR IGNORE INTO settings(key, value)
            VALUES(?, ?)
            """,
            (key, value)
        )

    conn.commit()
    conn.close()


# =========================
# SETTINGS
# =========================

def get_setting(key):

    conn = db()

    row = conn.execute(
        """
        SELECT value
        FROM settings
        WHERE key=?
        """,
        (key,)
    ).fetchone()

    conn.close()

    if row:
        return row["value"]

    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):

    conn = db()

    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, str(value))
    )

    conn.commit()
    conn.close()


def setting_int(key, fallback):

    try:
        return int(get_setting(key))

    except (TypeError, ValueError):

        return fallback


def feature_on(feature):

    return get_setting(
        f"feature_{feature}"
    ) == "1"


# =========================
# DYNAMIC MENU
# =========================

def get_markup():

    rows = []
    current = []

    for key in (
        "earn",
        "referral",
        "withdraw",
        "daily",
        "balance",
        "help"
    ):

        if feature_on(key):

            current.append(
                get_setting(
                    f"button_{key}"
                )
            )

            if len(current) == 2:

                rows.append(current)
                current = []

    if current:
        rows.append(current)

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True
    )


# =========================
# USER FUNCTIONS
# =========================

def register_user(user, referred_by=None):

    conn = db()

    existing = conn.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id=?
        """,
        (user.id,)
    ).fetchone()

    if not existing:

        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                first_name,
                referred_by
            )
            VALUES(?,?,?,?)
            """,
            (
                user.id,
                user.username or "",
                user.first_name or "",
                referred_by
            )
        )

    else:

        conn.execute(
            """
            UPDATE users
            SET username=?,
                first_name=?
            WHERE user_id=?
            """,
            (
                user.username or "",
                user.first_name or "",
                user.id
            )
        )

    conn.commit()
    conn.close()


def get_user(user_id):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    return row


def points(user_id):

    user = get_user(user_id)

    if not user:
        return 0

    return user["points"]


def add_points(user_id, amount):

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id=?
        """,
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def remove_points(user_id, amount):

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET points = MAX(points - ?, 0)
        WHERE user_id=?
        """,
        (amount, user_id)
    )

    conn.commit()
    conn.close()


# =========================
# START COMMAND
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referred_by = None

    if context.args:

        try:

            ref_id = int(context.args[0])

            if ref_id != user.id:

                referred_by = ref_id

        except ValueError:

            referred_by = None

    register_user(
        user,
        referred_by
    )

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn points.\n"
        "👥 Invite friends and earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Withdraw your earned points.\n\n"
        "👇 Select an option from the menu.",
        reply_markup=get_markup()
    )


# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id):

    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
)
