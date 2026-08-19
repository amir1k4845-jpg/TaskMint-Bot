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
# =========================
# EARN TASKS
# =========================

def create_default_task():

    conn = db()

    row = conn.execute(
        """
        SELECT id
        FROM channel_tasks
        LIMIT 1
        """
    ).fetchone()

    if not row:

        conn.execute(
            """
            INSERT INTO channel_tasks(
                title,
                channel,
                channel_url,
                reward,
                active
            )
            VALUES(?,?,?,?,?)
            """,
            (
                "Join AMIR 10M",
                "@Amir10m300",
                "https://t.me/Amir10m300",
                setting_int(
                    "reward_task",
                    TASK_REWARD
                ),
                1
            )
        )

        conn.commit()

    conn.close()


def get_channel_tasks():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM channel_tasks
        WHERE active=1
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def get_channel_task(task_id):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM channel_tasks
        WHERE id=?
        AND active=1
        """,
        (task_id,)
    ).fetchone()

    conn.close()

    return row


def task_done(user_id, task_key):

    conn = db()

    row = conn.execute(
        """
        SELECT id
        FROM completed_tasks
        WHERE user_id=?
        AND task_key=?
        """,
        (
            user_id,
            task_key
        )
    ).fetchone()

    conn.close()

    return row is not None


def save_task(user_id, task_key):

    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO completed_tasks(
            user_id,
            task_key
        )
        VALUES(?,?)
        """,
        (
            user_id,
            task_key
        )
    )

    conn.commit()
    conn.close()


async def earn_tasks(
    update,
    context
):

    if not feature_on("earn"):

        await update.message.reply_text(
            "⚠️ Earn Tasks feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return

    rows = get_channel_tasks()

    if not rows:

        await update.message.reply_text(
            "💰 EARN TASKS\n\n"
            "😔 বর্তমানে কোনো Task available নেই।\n\n"
            "পরে আবার চেষ্টা করো।",
            reply_markup=get_markup()
        )

        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"📢 {row['title']} "
                f"(+{row['reward']} Points)",
                url=row["channel_url"]
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                f"✅ Check Task #{row['id']}",
                callback_data=(
                    f"check_task_{row['id']}"
                )
            )
        ])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n"
        "📌 Task complete করার নিয়ম:\n"
        "1️⃣ প্রথমে Channel Join করো।\n"
        "2️⃣ Join করার পর নিচের "
        "✅ Check button চাপো।\n\n"
        "🎁 প্রতিটি Task একবারই reward দেবে।",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def task_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    data = query.data

    user_id = query.from_user.id

    if not data.startswith(
        "check_task_"
    ):
        return

    try:

        task_id = int(
            data.split("_")[2]
        )

    except (
        ValueError,
        IndexError
    ):

        await query.answer(
            "❌ Invalid Task",
            show_alert=True
        )

        return

    task = get_channel_task(
        task_id
    )

    if not task:

        await query.edit_message_text(
            "❌ এই Task আর available নেই।"
        )

        return

    task_key = (
        f"channel_{task_id}"
    )

    if task_done(
        user_id,
        task_key
    ):

        await query.edit_message_text(
            "⚠️ TASK ALREADY COMPLETED\n\n"
            "এই Task তুমি আগেই complete করেছো।\n\n"
            f"💰 Current Points: "
            f"{points(user_id)}"
        )

        return

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel"],
            user_id=user_id
        )

        if member.status in (
            "member",
            "administrator",
            "creator"
        ):

            add_points(
                user_id,
                task["reward"]
            )

            save_task(
                user_id,
                task_key
            )

            await query.edit_message_text(
                "🎉 TASK COMPLETED!\n\n"
                f"✅ Reward: +{task['reward']} Points\n"
                f"💰 Total Points: "
                f"{points(user_id)}"
            )

        else:

            await query.edit_message_text(
                "❌ TASK NOT COMPLETED\n\n"
                "আগে Channel-এ Join করো।\n"
                "তারপর আবার Check করো।"
            )

    except Exception as e:

        print(
            "Task check error:",
            e
        )

        await query.edit_message_text(
            "⚠️ Task verify করা যাচ্ছে না।\n\n"
            "কিছুক্ষণ পরে আবার চেষ্টা করো।"
        )


# =========================
# REFERRAL
# =========================

async def refer_earn(
    update,
    context
):

    if not feature_on("referral"):

        await update.message.reply_text(
            "⚠️ Referral feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return

    user = update.effective_user

    bot = await context.bot.get_me()

    referral_link = (
        f"https://t.me/{bot.username}"
        f"?start={user.id}"
    )

    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE referred_by=?
        AND referral_rewarded=1
        """,
        (user.id,)
    ).fetchone()

    conn.close()

    referrals = row["total"] if row else 0

    reward = setting_int(
        "reward_referral",
        REFERRAL_REWARD
    )

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        "💰 বন্ধুদের Invite করে points earn করো!\n\n"
        f"🎁 প্রতি successful referral: "
        f"+{reward} Points\n"
        f"👥 Total Referrals: {referrals}\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{referral_link}\n\n"
        "📢 Link টি বন্ধুদের সাথে Share করো।",
        reply_markup=get_markup()
    )


# =========================
# REFERRAL REWARD
# =========================

async def process_referral(
    user_id
):

    conn = db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if not user:

        conn.close()

        return

    referrer_id = user["referred_by"]

    if not referrer_id:

        conn.close()

        return

    if user["referral_rewarded"] == 1:

        conn.close()

        return

    if referrer_id == user_id:

        conn.close()

        return

    referrer = conn.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id=?
        """,
        (referrer_id,)
    ).fetchone()

    if not referrer:

        conn.close()

        return

    reward = setting_int(
        "reward_referral",
        REFERRAL_REWARD
    )

    conn.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id=?
        """,
        (
            reward,
            referrer_id
        )
    )

    conn.execute(
        """
        UPDATE users
        SET referral_rewarded=1
        WHERE user_id=?
        """,
        (user_id,)
    )

    conn.commit()
    conn.close()


# =========================
# DAILY BONUS
# =========================

async def daily_bonus(
    update,
    context
):

    if not feature_on("daily"):

        await update.message.reply_text(
            "⚠️ Daily Bonus feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:

        register_user(
            update.effective_user
        )

        user = get_user(
            user_id
        )

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    if user["last_bonus"] == today:

        await update.message.reply_text(
            "🎁 DAILY BONUS\n\n"
            "⏳ আজকের bonus তুমি already claim করেছো।\n\n"
            "🌅 আগামীকাল আবার claim করতে পারবে।\n\n"
            f"💰 Current Points: "
            f"{points(user_id)}",
            reply_markup=get_markup()
        )

        return

    reward = setting_int(
        "reward_daily",
        DAILY_REWARD
    )

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET points = points + ?,
            last_bonus = ?
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
        "🎉 DAILY BONUS CLAIMED!\n\n"
        f"🎁 Bonus: +{reward} Points\n"
        f"💰 Total Points: "
        f"{points(user_id)}\n\n"
        "⏰ আগামীকাল আবার claim করতে পারবে।",
        reply_markup=get_markup()
    )
# =========================
# WITHDRAW START
# =========================

async def withdraw_start(
    update,
    context
):

    if not feature_on("withdraw"):

        await update.message.reply_text(
            "⚠️ Withdraw feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    context.user_data.clear()

    user_id = update.effective_user.id

    balance = points(user_id)

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    if balance < minimum:

        await update.message.reply_text(
            "💳 WITHDRAW\n\n"
            f"💰 Your Balance: {balance} Points\n"
            f"📌 Minimum Withdraw: {minimum} Points\n\n"
            "❌ তোমার balance minimum withdraw-এর "
            "চেয়ে কম।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"💰 Available Points: {balance}\n"
        f"📌 Minimum Withdraw: {minimum}\n\n"
        "কত Points withdraw করতে চাও?\n\n"
        "Example: 100"
    )

    return AMOUNT


# =========================
# WITHDRAW AMOUNT
# =========================

async def withdraw_amount(
    update,
    context
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    try:

        amount = int(text)

    except ValueError:

        await update.message.reply_text(
            "❌ শুধু সংখ্যায় Amount পাঠাও।\n\n"
            "Example: 100"
        )

        return AMOUNT

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    balance = points(user_id)

    if amount < minimum:

        await update.message.reply_text(
            f"❌ Minimum withdraw হলো "
            f"{minimum} Points।\n\n"
            "আবার Amount পাঠাও।"
        )

        return AMOUNT

    if amount > balance:

        await update.message.reply_text(
            "❌ তোমার কাছে এত Points নেই।\n\n"
            f"💰 Available: {balance} Points\n\n"
            "আবার Amount পাঠাও।"
        )

        return AMOUNT

    context.user_data[
        "withdraw_amount"
    ] = amount

    await update.message.reply_text(
        "💳 WITHDRAW METHOD\n\n"
        "কোন Payment Method ব্যবহার করতে চাও?\n\n"
        "Example:\n"
        "Binance\n"
        "Bkash\n"
        "Nagad"
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
            "❌ Valid Payment Method পাঠাও।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
        "📱 PAYMENT ACCOUNT\n\n"
        f"💳 Method: {method}\n\n"
        "তোমার Payment Account Number/"
        "ID পাঠাও।"
    )

    return ACCOUNT


# =========================
# WITHDRAW ACCOUNT
# =========================

async def withdraw_account(
    update,
    context
):

    account = update.message.text.strip()

    if not account:

        await update.message.reply_text(
            "❌ Valid Account/ID পাঠাও।"
        )

        return ACCOUNT

    user = update.effective_user

    amount = context.user_data.get(
        "withdraw_amount"
    )

    method = context.user_data.get(
        "withdraw_method"
    )

    if not amount or not method:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Withdrawal session expired।\n\n"
            "আবার Withdraw শুরু করো।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    balance = points(user.id)

    if amount > balance:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ তোমার balance পরিবর্তন হয়েছে।\n\n"
            "আবার Withdraw চেষ্টা করো।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    # Deduct points only when request is created
    remove_points(
        user.id,
        amount
    )

    conn = db()

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor = conn.execute(
        """
        INSERT INTO withdrawals(
            user_id,
            username,
            amount,
            method,
            account,
            status,
            created_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            user.id,
            user.username or "",
            amount,
            method,
            account,
            "pending",
            now
        )
    )

    withdrawal_id = cursor.lastrowid

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ WITHDRAWAL REQUEST SENT!\n\n"
        f"🆔 Request: #{withdrawal_id}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n"
        "📌 Status: Pending\n\n"
        "⏳ Admin review করার পর payment process হবে।",
        reply_markup=get_markup()
    )

    # Notify admin
    if ADMIN_ID:

        try:

            username = (
                f"@{user.username}"
                if user.username
                else "No username"
            )

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 NEW WITHDRAWAL\n\n"
                    f"🆔 Request: #{withdrawal_id}\n"
                    f"👤 User ID: {user.id}\n"
                    f"👤 Username: {username}\n"
                    f"💰 Amount: {amount} Points\n"
                    f"💳 Method: {method}\n"
                    f"📱 Account: {account}"
                )
            )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    context.user_data.clear()

    return ConversationHandler.END


# =========================
# WITHDRAW CANCEL
# =========================

async def withdraw_cancel(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Withdraw cancelled।",
        reply_markup=get_markup()
    )

    return ConversationHandler.END


# =========================
# MY BALANCE
# =========================

async def my_balance(
    update,
    context
):

    if not feature_on("balance"):

        await update.message.reply_text(
            "⚠️ Balance feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:

        register_user(
            update.effective_user
        )

        user = get_user(
            user_id
        )

    conn = db()

    referral_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE referred_by=?
        AND referral_rewarded=1
        """,
        (user_id,)
    ).fetchone()

    task_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM completed_tasks
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    referrals = (
        referral_row["total"]
        if referral_row
        else 0
    )

    completed_tasks = (
        task_row["total"]
        if task_row
        else 0
    )

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    await update.message.reply_text(
        "📊 MY BALANCE\n\n"
        f"💰 Points: {user['points']}\n"
        f"👥 Referrals: {referrals}\n"
        f"✅ Completed Tasks: {completed_tasks}\n\n"
        f"💳 Minimum Withdraw: "
        f"{minimum} Points",
        reply_markup=get_markup()
    )
# =========================
# HELP
# =========================

async def help_menu(
    update,
    context
):

    if not feature_on("help"):

        await update.message.reply_text(
            "⚠️ Help feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )

        return

    await update.message.reply_text(
        "ℹ️ HELP & INFORMATION\n\n"

        "💰 Earn Tasks\n"
        "→ Channel/Task complete করে Points earn করো।\n\n"

        "👥 Refer & Earn\n"
        "→ Referral link share করে bonus earn করো।\n\n"

        "🎁 Daily Bonus\n"
        "→ প্রতিদিন একবার Daily Bonus claim করো।\n\n"

        "📊 My Balance\n"
        "→ তোমার Points, Referral এবং completed task দেখো।\n\n"

        "💳 Withdraw\n"
        "→ Minimum Points পূরণ হলে withdrawal request পাঠাও।\n\n"

        "⚠️ কোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।",

        reply_markup=get_markup()
    )


# =========================
# ADMIN PANEL
# =========================

def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "💰 Add Points",
                callback_data="admin_add_points"
            )
        ],

        [
            InlineKeyboardButton(
                "➖ Remove Points",
                callback_data="admin_remove_points"
            ),
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            )
        ],

        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ],

        [
            InlineKeyboardButton(
                "⚙️ Bot Settings",
                callback_data="admin_settings"
            )
        ],

    ])


async def admin_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Unauthorized."
        )

        return

    await update.message.reply_text(
        "👑 ADMIN PANEL\n\n"
        "নিচের menu থেকে একটি option select করো:",
        reply_markup=admin_keyboard()
    )


# =========================
# ADMIN USERS
# =========================

async def admin_user_search(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ Unauthorized",
            show_alert=True
        )

        return

    await query.message.reply_text(
        "👤 USER SEARCH\n\n"
        "যে User-এর তথ্য দেখতে চাও "
        "তার Telegram User ID পাঠাও।\n\n"
        "Example: 123456789"
    )

    context.user_data[
        "admin_user_search"
    ] = True


async def process_user_management(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return False

    if not context.user_data.get(
        "admin_user_search"
    ):

        return False

    text = update.message.text.strip()

    try:

        user_id = int(text)

    except ValueError:

        await update.message.reply_text(
            "❌ Valid Telegram User ID পাঠাও।"
        )

        return True

    user = get_user(user_id)

    if not user:

        await update.message.reply_text(
            "❌ এই User পাওয়া যায়নি।"
        )

        context.user_data.pop(
            "admin_user_search",
            None
        )

        return True

    conn = db()

    referral_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE referred_by=?
        AND referral_rewarded=1
        """,
        (user_id,)
    ).fetchone()

    task_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM completed_tasks
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    withdrawal_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM withdrawals
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    username = (
        f"@{user['username']}"
        if user["username"]
        else "No username"
    )

    await update.message.reply_text(
        "👤 USER INFORMATION\n\n"
        f"🆔 User ID: {user['user_id']}\n"
        f"👤 Username: {username}\n"
        f"📝 Name: {user['first_name']}\n\n"
        f"💰 Points: {user['points']}\n"
        f"👥 Referrals: {referral_row['total']}\n"
        f"✅ Tasks: {task_row['total']}\n"
        f"💳 Withdrawals: {withdrawal_row['total']}"
    )

    context.user_data.pop(
        "admin_user_search",
        None
    )

    return True


# =========================
# ADD POINTS MENU
# =========================

async def admin_add_points_menu(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        query.from_user.id
    ):

        return

    context.user_data[
        "points_action"
    ] = "add"

    context.user_data[
        "points_waiting_user"
    ] = True

    await query.message.reply_text(
        "➕ ADD POINTS\n\n"
        "প্রথমে User ID পাঠাও।\n\n"
        "Example: 123456789"
    )


# =========================
# REMOVE POINTS MENU
# =========================

async def admin_remove_points_menu(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        query.from_user.id
    ):

        return

    context.user_data[
        "points_action"
    ] = "remove"

    context.user_data[
        "points_waiting_user"
    ] = True

    await query.message.reply_text(
        "➖ REMOVE POINTS\n\n"
        "প্রথমে User ID পাঠাও।\n\n"
        "Example: 123456789"
    )


# =========================
# PROCESS POINTS USER
# =========================

async def process_points_user(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return False

    if not context.user_data.get(
        "points_waiting_user"
    ):

        return False

    text = update.message.text.strip()

    try:

        target_user = int(text)

    except ValueError:

        await update.message.reply_text(
            "❌ Valid User ID পাঠাও।"
        )

        return True

    user = get_user(target_user)

    if not user:

        await update.message.reply_text(
            "❌ এই User পাওয়া যায়নি।"
        )

        context.user_data.pop(
            "points_waiting_user",
            None
        )

        context.user_data.pop(
            "points_action",
            None
        )

        return True

    context.user_data[
        "points_user_id"
    ] = target_user

    context.user_data.pop(
        "points_waiting_user",
        None
    )

    action = context.user_data.get(
        "points_action"
    )

    if action == "add":

        message = (
            "➕ ADD POINTS\n\n"
            f"👤 User ID: {target_user}\n"
            f"💰 Current Points: {user['points']}\n\n"
            "কত Points add করতে চাও?"
        )

    else:

        message = (
            "➖ REMOVE POINTS\n\n"
            f"👤 User ID: {target_user}\n"
            f"💰 Current Points: {user['points']}\n\n"
            "কত Points remove করতে চাও?"
        )

    await update.message.reply_text(
        message
    )

    context.user_data[
        "points_waiting_amount"
    ] = True

    return True


# =========================
# PROCESS POINTS AMOUNT
# =========================

async def admin_points_action(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return False

    if not context.user_data.get(
        "points_waiting_amount"
    ):

        return False

    text = update.message.text.strip()

    try:

        amount = int(text)

    except ValueError:

        await update.message.reply_text(
            "❌ শুধু সংখ্যা পাঠাও।\n\n"
            "Example: 100"
        )

        return True

    if amount <= 0:

        await update.message.reply_text(
            "❌ Amount 0-এর বেশি হতে হবে।"
        )

        return True

    target_user = context.user_data.get(
        "points_user_id"
    )

    action = context.user_data.get(
        "points_action"
    )

    if not target_user:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Admin action expired।"
        )

        return True

    user = get_user(target_user)

    if not user:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ User পাওয়া যায়নি।"
        )

        return True

    if action == "add":

        add_points(
            target_user,
            amount
        )

        title = "➕ POINTS ADDED"

    else:

        remove_points(
            target_user,
            amount
        )

        title = "➖ POINTS REMOVED"

    new_balance = points(
        target_user
    )

    await update.message.reply_text(
        f"{title}\n\n"
        f"👤 User ID: {target_user}\n"
        f"💰 Amount: {amount} Points\n"
        f"📊 New Balance: {new_balance} Points"
    )

    try:

        await context.bot.send_message(
            chat_id=target_user,
            text=(
                f"{title}\n\n"
                f"💰 Amount: {amount} Points\n"
                f"📊 Your Balance: "
                f"{new_balance} Points"
            )
        )

    except Exception as e:

        print(
            "User points notification error:",
            e
        )

    context.user_data.clear()

    return True


# =========================
# ADMIN CANCEL
# =========================

async def admin_cancel(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Admin action cancelled।",
        reply_markup=get_markup()
        )
