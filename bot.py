import os
import sqlite3
import logging
from datetime import datetime, date

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "PUT_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

CHANNEL_USERNAME = "@TaskMint_v1"
DB_NAME = "taskmint.db"

REFERRAL_REWARD = 20
DAILY_BONUS = 10
MIN_WITHDRAW = 100

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            referred_by INTEGER,
            referral_rewarded INTEGER DEFAULT 0,
            daily_bonus_date TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT,
            instruction TEXT,
            reward REAL DEFAULT 0,
            total_slots INTEGER DEFAULT 0,
            completed_slots INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof TEXT,
            status TEXT DEFAULT 'pending',
            reward REAL DEFAULT 0,
            submitted_at TEXT,
            reviewed_at TEXT,
            UNIQUE(task_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            address TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            reviewed_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# USER DATABASE
# =========================================================

def get_user(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    return result


def create_user(
    user_id,
    username,
    first_name,
    referred_by=None
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO users (
                user_id,
                username,
                first_name,
                balance,
                referred_by,
                created_at
            )
            VALUES (?, ?, ?, 0, ?, ?)
        """, (
            user_id,
            username,
            first_name,
            referred_by,
            datetime.utcnow().isoformat()
        ))

    else:
        cur.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            user_id
        ))

    conn.commit()
    conn.close()


def add_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


# =========================================================
# TASK FUNCTIONS
# =========================================================

def get_task(task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,)
    )

    task = cur.fetchone()
    conn.close()

    return task


def task_has_submission(task_id, user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM task_submissions
        WHERE task_id = ? AND user_id = ?
    """, (
        task_id,
        user_id
    ))

    result = cur.fetchone()
    conn.close()

    return result


def available_slots(task):
    if task["total_slots"] <= 0:
        return 999999999

    return max(
        0,
        task["total_slots"] - task["completed_slots"]
    )


def automatically_disable_task(task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE tasks
        SET active = 0
        WHERE id = ?
        AND total_slots > 0
        AND completed_slots >= total_slots
    """, (task_id,))

    conn.commit()
    conn.close()


# =========================================================
# MENUS
# =========================================================

def user_menu():
    keyboard = [
        [
            "💰 Earn Tasks",
            "👥 Refer & Earn"
        ],
        [
            "💳 Withdraw",
            "🎁 Daily Bonus"
        ],
        [
            "📊 My Balance",
            "ℹ️ Help"
        ]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


def admin_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Create Task",
                callback_data="admin_create"
            ),
            InlineKeyboardButton(
                "📋 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "📥 Pending Submissions",
                callback_data="admin_pending"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Withdrawals",
                callback_data="admin_withdrawals"
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================================================
# START
# =========================================================

async def start(update, context):

    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            ref = int(context.args[0])

            if ref != user.id:
                referred_by = ref

        except:
            pass

    old_user = get_user(user.id)

    create_user(
        user.id,
        user.username,
        user.first_name,
        referred_by
    )

    if (
        referred_by
        and not old_user
        and referred_by != user.id
    ):
        add_balance(
            referred_by,
            REFERRAL_REWARD
        )

        try:
            await context.bot.send_message(
                referred_by,
                f"🎉 New referral!\n\n"
                f"💰 +{REFERRAL_REWARD} reward added."
            )
        except:
            pass

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn rewards.\n"
        "👥 Invite friends and earn rewards.\n"
        "🎁 Claim your daily bonus.",
        reply_markup=user_menu()
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin_command(update, context):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Unauthorized."
        )
        return

    await update.message.reply_text(
        "🛠️ Admin Panel",
        reply_markup=admin_menu()
    )


init_db()
