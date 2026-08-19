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

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_FILE = "taskmint.db"

AMOUNT, METHOD, ACCOUNT = range(3)

DEFAULT_MIN_WITHDRAW = 100
DEFAULT_REFERRAL_REWARD = 20
DEFAULT_DAILY_REWARD = 10
DEFAULT_TASK_REWARD = 10


# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            referral_rewarded INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT,
            channel_url TEXT,
            title TEXT,
            reward INTEGER DEFAULT 10,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            task_id INTEGER,
            completed_at TEXT,
            PRIMARY KEY(user_id, task_id)
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
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================
# SETTINGS
# =========================

def get_setting(key, default=None):

    conn = db()

    row = conn.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()

    conn.close()

    if row:
        return row["value"]

    return default


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


def get_int_setting(key, default):

    try:
        return int(
            get_setting(key, default)
        )
    except Exception:
        return default


# =========================
# DEFAULT SETTINGS
# =========================

def init_settings():

    defaults = {

        "min_withdraw":
            DEFAULT_MIN_WITHDRAW,

        "referral_reward":
            DEFAULT_REFERRAL_REWARD,

        "daily_reward":
            DEFAULT_DAILY_REWARD,

        "default_task_reward":
            DEFAULT_TASK_REWARD,

        "button_earn":
            "💰 Earn Tasks",

        "button_referral":
            "👥 Refer & Earn",

        "button_withdraw":
            "💳 Withdraw",

        "button_daily":
            "🎁 Daily Bonus",

        "button_balance":
            "📊 My Balance",

        "button_help":
            "ℹ️ Help",

        "feature_earn":
            "1",

        "feature_referral":
            "1",

        "feature_withdraw":
            "1",

        "feature_daily":
            "1",

        "feature_balance":
            "1",

        "feature_help":
            "1",
    }

    for key, value in defaults.items():

        if get_setting(key) is None:
            set_setting(key, value)


# =========================
# USER MENU
# =========================

def build_markup():

    keyboard = []

    if get_setting(
        "feature_earn",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_earn",
                "💰 Earn Tasks"
            )
        ])

    if get_setting(
        "feature_referral",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_referral",
                "👥 Refer & Earn"
            )
        ])

    if get_setting(
        "feature_withdraw",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_withdraw",
                "💳 Withdraw"
            )
        ])

    if get_setting(
        "feature_daily",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_daily",
                "🎁 Daily Bonus"
            )
        ])

    if get_setting(
        "feature_balance",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_balance",
                "📊 My Balance"
            )
        ])

    if get_setting(
        "feature_help",
        "1"
    ) == "1":

        keyboard.append([
            get_setting(
                "button_help",
                "ℹ️ Help"
            )
        ])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id):

    return (
        ADMIN_ID
        and user_id == ADMIN_ID
    )


# =========================
# USER
# =========================

def ensure_user(user):

    conn = db()

    row = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user.id,)
    ).fetchone()

    if not row:

        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                points
            )
            VALUES(?, ?, 0)
            """,
            (
                user.id,
                user.username
            )
        )

    else:

        conn.execute(
            """
            UPDATE users
            SET username=?
            WHERE user_id=?
            """,
            (
                user.username,
                user.id
            )
        )

    conn.commit()
    conn.close()


def points(user_id):

    conn = db()

    row = conn.execute(
        """
        SELECT points
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    if not row:
        return 0

    return row["points"]


def add_points(
    user_id,
    amount
):

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET points=points+?
        WHERE user_id=?
        """,
        (
            amount,
            user_id
        )
    )

    conn.commit()
    conn.close()


def remove_points(
    user_id,
    amount
):

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET points=MAX(points-?, 0)
        WHERE user_id=?
        """,
        (
            amount,
            user_id
        )
    )

    conn.commit()
    conn.close()


# =========================
# REFERRAL
# =========================

async def process_referral(
    user_id,
    referrer_id,
    context
):

    if not referrer_id:
        return

    if user_id == referrer_id:
        return

    conn = db()

    row = conn.execute(
        """
        SELECT referred_by
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if not row:
        conn.close()
        return

    if row["referred_by"]:

        conn.close()
        return

    conn.execute(
        """
        UPDATE users
        SET referred_by=?
        WHERE user_id=?
        """,
        (
            referrer_id,
            user_id
        )
    )

    conn.commit()
    conn.close()

    reward = get_int_setting(
        "referral_reward",
        DEFAULT_REFERRAL_REWARD
    )

    add_points(
        referrer_id,
        reward
    )

    try:

        await context.bot.send_message(
            chat_id=referrer_id,
            text=(
                "🎉 Referral Successful!\n\n"
                f"👤 New user joined.\n"
                f"💰 +{reward} Points added!"
            )
        )

    except Exception:
        pass


async def referral(
    update,
    context
):

    user_id = update.effective_user.id

    bot_username = (
        context.bot.username
        or "TaskMintBot"
    )

    link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    reward = get_int_setting(
        "referral_reward",
        DEFAULT_REFERRAL_REWARD
    )

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        "বন্ধুদের invite করো এবং "
        "points earn করো।\n\n"
        f"💰 প্রতি referral: +{reward} Points\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{link}",
        reply_markup=build_markup()
    )


# =========================
# DAILY BONUS
# =========================

async def daily_bonus(
    update,
    context
):

    user_id = update.effective_user.id

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    conn = db()

    row = conn.execute(
        """
        SELECT last_daily
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if row and row["last_daily"] == today:

        conn.close()

        await update.message.reply_text(
            "⏳ তুমি আজকের Daily Bonus already নিয়েছো।\n\n"
            "আগামীকাল আবার নিতে পারবে।",
            reply_markup=build_markup()
        )

        return

    reward = get_int_setting(
        "daily_reward",
        DEFAULT_DAILY_REWARD
    )

    conn.execute(
        """
        UPDATE users
        SET points=points+?,
            last_daily=?
        WHERE user_id=?
        """,
        (
            reward,
            today,
            user_id
        )
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎁 DAILY BONUS\n\n"
        f"✅ আজকের bonus পেয়েছো!\n"
        f"💰 +{reward} Points\n\n"
        f"💳 Balance: {points(user_id)} Points",
        reply_markup=build_markup()
    )


# =========================
# BALANCE
# =========================

async def my_balance(
    update,
    context
):

    user_id = update.effective_user.id

    await update.message.reply_text(
        "📊 MY BALANCE\n\n"
        f"💰 Points: {points(user_id)}",
        reply_markup=build_markup()
    )


# =========================
# HELP
# =========================

async def help_cmd(
    update,
    context
):

    await update.message.reply_text(
        "ℹ️ TASKMINT HELP\n\n"
        "💰 Earn Tasks — Tasks complete করে points earn করো.\n"
        "👥 Refer & Earn — Friends invite করে earn করো.\n"
        "🎁 Daily Bonus — প্রতিদিন bonus নাও.\n"
        "📊 My Balance — তোমার points দেখো.\n"
        "💳 Withdraw — Points withdraw request করো.",
        reply_markup=build_markup()
    )


# =========================
# START
# =========================

async def start(
    update,
    context
):

    user = update.effective_user

    ensure_user(user)

    referrer_id = None

    if context.args:

        try:
            referrer_id = int(
                context.args[0]
            )
        except Exception:
            referrer_id = None

    if referrer_id:

        await process_referral(
            user.id,
            referrer_id,
            context
        )

    await update.message.reply_text(
        "🚀 TASKMINT BOT\n\n"
        "Welcome!\n\n"
        "নিচের menu থেকে option নির্বাচন করো।",
        reply_markup=build_markup()
    )


# =========================
# TASKS
# =========================

def create_default_task():

    conn = db()

    row = conn.execute(
        "SELECT COUNT(*) FROM tasks"
    ).fetchone()[0]

    if row == 0:

        conn.execute(
            """
            INSERT INTO tasks(
                channel,
                channel_url,
                title,
                reward,
                active
            )
            VALUES(?, ?, ?, ?, 1)
            """,
            (
                "@Amir10m300",
                "https://t.me/Amir10m300",
                "📢 Join Channel",
                get_int_setting(
                    "default_task_reward",
                    DEFAULT_TASK_REWARD
                )
            )
        )

        conn.commit()

    conn.close()


def get_channel_tasks():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE active=1
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def add_channel_task(
    channel,
    channel_url,
    title,
    reward
):

    conn = db()

    conn.execute(
        """
        INSERT INTO tasks(
            channel,
            channel_url,
            title,
            reward,
            active
        )
        VALUES(?, ?, ?, ?, 1)
        """,
        (
            channel,
            channel_url,
            title,
            reward
        )
    )

    conn.commit()
    conn.close()


def delete_channel_task(
    task_id
):

    conn = db()

    conn.execute(
        """
        UPDATE tasks
        SET active=0
        WHERE id=?
        """,
        (task_id,)
    )

    conn.commit()
    conn.close()


# =========================
# EARN TASKS
# =========================

async def earn_tasks(
    update,
    context
):

    user_id = update.effective_user.id

    rows = get_channel_tasks()

    if not rows:

        await update.message.reply_text(
            "📋 এখন কোনো task available নেই।",
            reply_markup=build_markup()
        )

        return

    conn = db()

    completed = conn.execute(
        """
        SELECT task_id
        FROM completed_tasks
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    completed_ids = {
        row["task_id"]
        for row in completed
    }

    buttons = []

    text = (
        "💰 EARN TASKS\n\n"
        "Task complete করে points earn করো:\n\n"
    )

    for row in rows:

        if row["id"] in completed_ids:
            continue

        text += (
            f"📌 {row['title']}\n"
            f"💰 Reward: +{row['reward']} Points\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                row["title"],
                url=row["channel_url"]
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                f"✅ Check Task (+{row['reward']})",
                callback_data=(
                    f"check_task_{row['id']}"
                )
            )
        ])

    if not buttons:

        text = (
            "🎉 তুমি সব available task complete করে ফেলেছো!"
        )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================
# TASK CALLBACK
# =========================

async def task_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    try:

        task_id = int(
            query.data.split("_")[2]
        )

    except Exception:

        return

    conn = db()

    task = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE id=? AND active=1
        """,
        (task_id,)
    ).fetchone()

    if not task:

        conn.close()

        await query.answer(
            "❌ Task পাওয়া যায়নি।",
            show_alert=True
        )

        return

    done = conn.execute(
        """
        SELECT 1
        FROM completed_tasks
        WHERE user_id=? AND task_id=?
        """,
        (
            user_id,
            task_id
        )
    ).fetchone()

    if done:

        conn.close()

        await query.answer(
            "✅ Task already completed.",
            show_alert=True
        )

        return

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel"],
            user_id=user_id
        )

        status = member.status

        allowed = status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception:

        conn.close()

        await query.answer(
            "❌ আগে channel-এ join করো।",
            show_alert=True
        )

        return

    if not allowed:

        conn.close()

        await query.answer(
            "❌ আগে channel-এ join করো।",
            show_alert=True
        )

        return

    conn.execute(
        """
        INSERT INTO completed_tasks(
            user_id,
            task_id,
            completed_at
        )
        VALUES(?, ?, ?)
        """,
        (
            user_id,
            task_id,
            datetime.now().isoformat()
        )
    )

    conn.execute(
        """
        UPDATE users
        SET points=points+?
        WHERE user_id=?
        """,
        (
            task["reward"],
            user_id
        )
    )

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "🎉 TASK COMPLETED!\n\n"
        f"📌 {task['title']}\n"
        f"💰 +{task['reward']} Points\n\n"
        f"📊 Balance: {points(user_id)} Points"
    )


# =========================
# WITHDRAW START
# =========================

async def withdraw_start(
    update,
    context
):

    user_id = update.effective_user.id

    minimum = get_int_setting(
        "min_withdraw",
        DEFAULT_MIN_WITHDRAW
    )

    if points(user_id) < minimum:

        await update.message.reply_text(
            "❌ তোমার পর্যাপ্ত points নেই।\n\n"
            f"💳 Minimum Withdraw: {minimum} Points\n"
            f"💰 Current Balance: {points(user_id)}",
            reply_markup=build_markup()
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"Minimum: {minimum} Points\n"
        f"Balance: {points(user_id)} Points\n\n"
        "কত Points withdraw করতে চাও?\n"
        "শুধু amount পাঠাও।\n\n"
        "Cancel করতে /cancel লিখো।"
    )

    return AMOUNT


# =========================
# WITHDRAW AMOUNT
# =========================

async def withdraw_amount(
    update,
    context
):

    try:

        amount = int(
            update.message.text.strip()
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Amount অবশ্যই সংখ্যা হতে হবে।"
        )

        return AMOUNT

    minimum = get_int_setting(
        "min_withdraw",
        DEFAULT_MIN_WITHDRAW
    )

    user_id = update.effective_user.id

    if amount < minimum:

        await update.message.reply_text(
            f"❌ Minimum withdraw {minimum} Points."
        )

        return AMOUNT

    if amount > points(user_id):

        await update.message.reply_text(
            "❌ তোমার balance-এর চেয়ে বেশি withdraw করতে পারবে না।"
        )

        return AMOUNT

    context.user_data[
        "withdraw_amount"
    ] = amount

    await update.message.reply_text(
        "💳 Payment Method লিখো।\n\n"
        "Example: Binance"
    )

    return METHOD


# =========================
# WITHDRAW METHOD
# =========================

async def withdraw_method(
    update,
    context
):

    method = update.message.text.strip()

    if not method:

        await update.message.reply_text(
            "❌ Payment method লিখো।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
