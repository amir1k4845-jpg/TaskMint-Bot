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
    ConversationHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = 7003609983
CHANNEL_USERNAME = "@Amir10m300"
CHANNEL_URL = "https://t.me/Amir10m300"

TASK_REWARD = 10
REFERRAL_REWARD = 20
MIN_WITHDRAW = 100
DAILY_REWARD = 10

DB_FILE = "taskmint.db"

AMOUNT, METHOD, ACCOUNT = range(3)


# =========================
# DATABASE
# =========================

def db():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()

    c.execute("""
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        user_id INTEGER,
        task_id TEXT,
        PRIMARY KEY(user_id, task_id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        amount INTEGER,
        method TEXT,
        account TEXT,
        status TEXT DEFAULT 'pending'
    )
    """)

    c.commit()
    c.close()


def register_user(user, referrer=None):
    c = db()

    old = c.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user.id,)
    ).fetchone()

    if not old:
        c.execute("""
        INSERT INTO users
        (user_id,username,first_name,referred_by)
        VALUES(?,?,?,?)
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            referrer
        ))

        if referrer and referrer != user.id:
            c.execute("""
            UPDATE users
            SET points=points+?,
                referral_rewarded=1
            WHERE user_id=?
            """, (
                REFERRAL_REWARD,
                referrer
            ))

    else:
        c.execute("""
        UPDATE users
        SET username=?, first_name=?
        WHERE user_id=?
        """, (
            user.username or "",
            user.first_name or "",
            user.id
        ))

    c.commit()
    c.close()


def points(uid):
    c = db()
    r = c.execute(
        "SELECT points FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()
    c.close()
    return r["points"] if r else 0


def add_points(uid, amount):
    c = db()
    c.execute(
        "UPDATE users SET points=points+? WHERE user_id=?",
        (amount, uid)
    )
    c.commit()
    c.close()


def remove_points(uid, amount):
    c = db()
    c.execute(
        "UPDATE users SET points=MAX(points-?,0) WHERE user_id=?",
        (amount, uid)
    )
    c.commit()
    c.close()


def task_done(uid, task):
    c = db()
    r = c.execute(
        "SELECT 1 FROM tasks WHERE user_id=? AND task_id=?",
        (uid, task)
    ).fetchone()
    c.close()
    return r is not None


def save_task(uid, task):
    c = db()
    c.execute(
        "INSERT OR IGNORE INTO tasks VALUES(?,?)",
        (uid, task)
    )
    c.commit()
    c.close()


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        pass


def web_server():
    HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    ).serve_forever()


# =========================
# MENU
# =========================

MENU = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]

MARKUP = ReplyKeyboardMarkup(
    MENU,
    resize_keyboard=True
)


# =========================
# START
# =========================

async def start(update, context):

    user = update.effective_user

    ref = None

    if context.args:
        try:
            ref = int(context.args[0])
        except:
            ref = None

    register_user(user, ref)

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করো।\n"
        "👥 Friends invite করে Points earn করো।\n"
        "🎁 Daily Bonus নাও।\n"
        "💳 Points withdraw করো।\n\n"
        "নিচের Menu থেকে শুরু করো 👇",
        reply_markup=MARKUP
    )


async def help_cmd(update, context):

    await update.message.reply_text(
        "ℹ️ TaskMint Help\n\n"
        "/start - Bot শুরু\n"
        "/help - Help\n"
        "/admin - Admin Panel\n\n"
        "যেকোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।"
    )


# =========================
# EARN TASK
# =========================

async def earn_tasks(update, context):

    keys = [
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
        "📢 Channel Join করো\n\n"
        f"🎁 Reward: +{TASK_REWARD} Points\n\n"
        "Join করার পর Check Task চাপো।",
        reply_markup=InlineKeyboardMarkup(keys)
    )
