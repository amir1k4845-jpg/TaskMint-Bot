import os
import sqlite3
import threading
import re
import asyncio

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

DB_FILE = "taskmint.db"

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100


# =========================
# WITHDRAW & TASK STATES
# =========================

AMOUNT, METHOD, ACCOUNT = range(3)
TASK_TITLE, TASK_CHANNEL, TASK_URL, TASK_REWARD_STATE = range(3, 7)


# =========================
# DEFAULT BUTTONS
# =========================

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
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
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
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (key, value)
        )

    conn.commit()
    conn.close()
    
# =========================
# SETTINGS HELPERS
# =========================

def get_setting(key):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    conn = db()
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
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
    return get_setting(f"feature_{feature}") == "1"


# =========================
# DYNAMIC MENU
# =========================

def get_markup():
    rows = []
    current = []

    for key in ("earn", "referral", "withdraw", "daily", "balance", "help"):
        if feature_on(key):
            current.append(get_setting(f"button_{key}"))
            if len(current) == 2:
                rows.append(current)
                current = []

    if current:
        rows.append(current)

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# =========================
# USER FUNCTIONS
# =========================

def register_user(user, referred_by=None):
    conn = db()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()

    if not existing:
        conn.execute(
            "INSERT INTO users(user_id, username, first_name, referred_by) VALUES(?,?,?,?)",
            (user.id, user.username or "", user.first_name or "", referred_by)
        )
    else:
        conn.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (user.username or "", user.first_name or "", user.id)
        )

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def points(user_id):
    user = get_user(user_id)
    return user["points"] if user else 0


def add_points(user_id, amount):
    conn = db()
    conn.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def remove_points(user_id, amount):
    conn = db()
    conn.execute("UPDATE users SET points = MAX(points - ?, 0) WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


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

    register_user(user, referred_by)

    if referred_by:
        await process_referral(user.id)

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
# EARN TASKS
# =========================

def create_default_task():
    conn = db()
    row = conn.execute("SELECT id FROM channel_tasks LIMIT 1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO channel_tasks(title, channel, channel_url, reward, active) VALUES(?,?,?,?,?)",
            ("Join Official Channel", "@Telegram", "https://t.me/Telegram", setting_int("reward_task", TASK_REWARD), 1)
        )
        conn.commit()
    conn.close()


def get_channel_tasks():
    conn = db()
    rows = conn.execute("SELECT * FROM channel_tasks WHERE active=1 ORDER BY id ASC").fetchall()
    conn.close()
    return rows


def get_channel_task(task_id):
    conn = db()
    row = conn.execute("SELECT * FROM channel_tasks WHERE id=? AND active=1", (task_id,)).fetchone()
    conn.close()
    return row


def task_done(user_id, task_key):
    conn = db()
    row = conn.execute("SELECT id FROM completed_tasks WHERE user_id=? AND task_key=?", (user_id, task_key)).fetchone()
    conn.close()
    return row is not None


def save_task(user_id, task_key):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO completed_tasks(user_id, task_key) VALUES(?,?)", (user_id, task_key))
    conn.commit()
    conn.close()


async def earn_tasks(update, context):
    if not feature_on("earn"):
        await update.message.reply_text("⚠️ Earn Tasks feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    rows = get_channel_tasks()
    if not rows:
        await update.message.reply_text(
            "💰 EARN TASKS\n\n😔 বর্তমানে কোনো Task available নেই।\n\nপরে আবার চেষ্টা করো।",
            reply_markup=get_markup()
        )
        return

    buttons = []
    for row in rows:
        buttons.append([InlineKeyboardButton(f"📢 {row['title']} (+{row['reward']} Points)", url=row["channel_url"])])
        buttons.append([InlineKeyboardButton(f"✅ Check Task #{row['id']}", callback_data=f"check_task_{row['id']}")])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n📌 Task complete করার নিয়ম:\n1️⃣ প্রথমে Channel Join করো।\n2️⃣ Join করার পর নিচের ✅ Check button চাপো।\n\n🎁 প্রতিটি Task একবারই reward দেবে।",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def task_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not data.startswith("check_task_"):
        return

    try:
        task_id = int(data.split("_")[2])
    except (ValueError, IndexError):
        await query.answer("❌ Invalid Task", show_alert=True)
        return

    task = get_channel_task(task_id)
    if not task:
        await query.edit_message_text("❌ এই Task আর available নেই।")
        return

    task_key = f"channel_{task_id}"
    if task_done(user_id, task_key):
        await query.edit_message_text(f"⚠️ TASK ALREADY COMPLETED\n\nএই Task তুমি আগেই complete করেছো।\n\n💰 Current Points: {points(user_id)}")
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel"], user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            add_points(user_id, task["reward"])
            save_task(user_id, task_key)
            await query.edit_message_text(f"🎉 TASK COMPLETED!\n\n✅ Reward: +{task['reward']} Points\n💰 Total Points: {points(user_id)}")
        else:
            await query.edit_message_text("❌ TASK NOT COMPLETED\n\nআগে Channel-এ Join করো।\nতারপর আবার Check করো।")
    except Exception as e:
        print("Task check error:", e)
        await query.edit_message_text("⚠️ Task verify করা যাচ্ছে না।\n\nকিছুক্ষণ পরে আবার চেষ্টা করো।")


# =========================
# REFERRAL & DAILY BONUS
# =========================

async def refer_earn(update, context):
    if not feature_on("referral"):
        await update.message.reply_text("⚠️ Referral feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    user = update.effective_user
    bot = await context.bot.get_me()
    referral_link = f"https://t.me/{bot.username}?start={user.id}"

    conn = db()
    row = conn.execute("SELECT COUNT(*) AS total FROM users WHERE referred_by=? AND referral_rewarded=1", (user.id,)).fetchone()
    conn.close()

    referrals = row["total"] if row else 0
    reward = setting_int("reward_referral", REFERRAL_REWARD)

    await update.message.reply_text(
        f"👥 REFER & EARN\n\n💰 বন্ধুদের Invite করে points earn করো!\n\n"
        f"🎁 প্রতি successful referral: +{reward} Points\n"
        f"👥 Total Referrals: {referrals}\n\n"
        f"🔗 তোমার Referral Link:\n{referral_link}\n\n📢 Link টি বন্ধুদের সাথে Share করো।",
        reply_markup=get_markup()
    )


async def process_referral(user_id):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not user or not user["referred_by"] or user["referral_rewarded"] == 1 or user["referred_by"] == user_id:
        conn.close()
        return

    referrer_id = user["referred_by"]
    reward = setting_int("reward_referral", REFERRAL_REWARD)

    conn.execute("UPDATE users SET points = points + ? WHERE user_id=?", (reward, referrer_id))
    conn.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


async def daily_bonus(update, context):
    if not feature_on("daily"):
        await update.message.reply_text("⚠️ Daily Bonus feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        register_user(update.effective_user)
        user = get_user(user_id)

    today = datetime.now().strftime("%Y-%m-%d")

    if user["last_bonus"] == today:
        await update.message.reply_text(
            f"🎁 DAILY BONUS\n\n⏳ আজকের bonus তুমি already claim করেছো।\n\n🌅 আগামীকাল আবার claim করতে পারবে।\n\n💰 Current Points: {points(user_id)}",
            reply_markup=get_markup()
        )
        return

    reward = setting_int("reward_daily", DAILY_REWARD)
    conn = db()
    conn.execute("UPDATE users SET points = points + ?, last_bonus = ? WHERE user_id=?", (reward, today, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎉 DAILY BONUS CLAIMED!\n\n🎁 Bonus: +{reward} Points\n💰 Total Points: {points(user_id)}\n\n⏰ আগামীকাল আবার claim করতে পারবে।",
        reply_markup=get_markup()
    )


# =========================
# WITHDRAW SYSTEM
# =========================

async def withdraw_start(update, context):
    if not feature_on("withdraw"):
        await update.message.reply_text("⚠️ Withdraw feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return ConversationHandler.END

    context.user_data.clear()
    user_id = update.effective_user.id
    balance = points(user_id)
    minimum = setting_int("min_withdraw", MIN_WITHDRAW)

    if balance < minimum:
        await update.message.reply_text(
            f"💳 WITHDRAW\n\n💰 Your Balance: {balance} Points\n📌 Minimum Withdraw: {minimum} Points\n\n❌ তোমার balance minimum withdraw-এর চেয়ে কম।",
            reply_markup=get_markup()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"💳 WITHDRAW\n\n💰 Available Points: {balance}\n📌 Minimum Withdraw: {minimum}\n\nকত Points withdraw করতে চাও?\n\nExample: 100"
    )
    return AMOUNT


async def withdraw_amount(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text("❌ শুধু সংখ্যায় Amount পাঠাও।\n\nExample: 100")
        return AMOUNT

    minimum = setting_int("min_withdraw", MIN_WITHDRAW)
    balance = points(user_id)

    if amount < minimum:
        await update.message.reply_text(f"❌ Minimum withdraw হলো {minimum} Points।\n\nআবার Amount পাঠাও।")
        return AMOUNT

    if amount > balance:
        await update.message.reply_text(f"❌ তোমার কাছে এত Points নেই।\n\n💰 Available: {balance} Points\n\nআবার Amount পাঠাও।")
        return AMOUNT

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text("💳 WITHDRAW METHOD\n\nকোন Payment Method ব্যবহার করতে চাও?\n\nExample:\nBinance\nBkash\nNagad")
    return METHOD


async def withdraw_method(update, context):
    method = update.message.text.strip()
    if not method:
        await update.message.reply_text("❌ Valid Payment Method পাঠাও।")
        return METHOD

    context.user_data["withdraw_method"] = method
    await update.message.reply_text(f"📱 PAYMENT ACCOUNT\n\n💳 Method: {method}\n\nতোমার Payment Account Number/ID পাঠাও।")
    return ACCOUNT


async def withdraw_account(update, context):
    account = update.message.text.strip()
    if not account:
        await update.message.reply_text("❌ Valid Account/ID পাঠাও।")
        return ACCOUNT

    user = update.effective_user
    amount = context.user_data.get("withdraw_amount")
    method = context.user_data.get("withdraw_method")

    if not amount or not method or amount > points(user.id):
        context.user_data.clear()
        await update.message.reply_text("❌ Session expired or balance changed।", reply_markup=get_markup())
        return ConversationHandler.END

    remove_points(user.id, amount)
    conn = db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO withdrawals(user_id, username, amount, method, account, status, created_at) VALUES(?,?,?,?,?,?,?)",
        (user.id, user.username or "", amount, method, account, "pending", now)
    )
    withdrawal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ WITHDRAWAL REQUEST SENT!\n\n🆔 Request: #{withdrawal_id}\n💰 Amount: {amount} Points\n💳 Method: {method}\n📱 Account: {account}\n📌 Status: Pending\n\n⏳ Admin review করার পর payment process হবে।",
        reply_markup=get_markup()
    )

    if ADMIN_ID:
        try:
            username = f"@{user.username}" if user.username else "No username"
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔔 NEW WITHDRAWAL\n\n🆔 Request: #{withdrawal_id}\n👤 User ID: {user.id}\n👤 Username: {username}\n💰 Amount: {amount} Points\n💳 Method: {method}\n📱 Account: {account}"
            )
        except Exception as e:
            print("Admin notification error:", e)

    context.user_data.clear()
    return ConversationHandler.END


async def withdraw_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Withdraw cancelled।", reply_markup=get_markup())
    return ConversationHandler.END
                       
# =========================
# BALANCE & HELP
# =========================

async def my_balance(update, context):
    if not feature_on("balance"):
        await update.message.reply_text("⚠️ Balance feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        register_user(update.effective_user)
        user = get_user(user_id)

    conn = db()
    referral_row = conn.execute("SELECT COUNT(*) AS total FROM users WHERE referred_by=? AND referral_rewarded=1", (user_id,)).fetchone()
    task_row = conn.execute("SELECT COUNT(*) AS total FROM completed_tasks WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    referrals = referral_row["total"] if referral_row else 0
    completed_tasks = task_row["total"] if task_row else 0
    minimum = setting_int("min_withdraw", MIN_WITHDRAW)

    await update.message.reply_text(
        f"📊 MY BALANCE\n\n💰 Points: {user['points']}\n👥 Referrals: {referrals}\n✅ Completed Tasks: {completed_tasks}\n\n💳 Minimum Withdraw: {minimum} Points",
        reply_markup=get_markup()
    )


async def help_menu(update, context):
    if not feature_on("help"):
        await update.message.reply_text("⚠️ Help feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    await update.message.reply_text(
        "ℹ️ HELP & INFORMATION\n\n"
        "💰 Earn Tasks\n→ Channel/Task complete করে Points earn করো।\n\n"
        "👥 Refer & Earn\n→ Referral link share করে bonus earn করো।\n\n"
        "🎁 Daily Bonus\n→ প্রতিদিন একবার Daily Bonus claim করো।\n\n"
        "📊 My Balance\n→ তোমার Points, Referral এবং completed task দেখো।\n\n"
        "💳 Withdraw\n→ Minimum Points পূরণ হলে withdrawal request পাঠাও।\n\n"
        "⚠️ কোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।",
        reply_markup=get_markup()
    )


# =========================
# ADMIN PANEL MAIN
# =========================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("💰 Add Points", callback_data="admin_add_points")
        ],
        [
            InlineKeyboardButton("➖ Remove Points", callback_data="admin_remove_points"),
            InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals")
        ],
        [
            InlineKeyboardButton("📢 Manage Tasks", callback_data="admin_manage_tasks"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
        ],
        [
            InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings")
        ],
    ])


async def admin_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return

    await update.message.reply_text("👑 ADMIN PANEL\n\nনিচের menu থেকে একটি option select করো:", reply_markup=admin_keyboard())


# =========================
# ADMIN TASK MANAGEMENT
# =========================

async def admin_manage_tasks(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    tasks = get_channel_tasks()
    text = "📢 TASK MANAGEMENT\n\nবর্তমান সক্রিয় টাস্কসমূহ:\n"
    buttons = []

    for task in tasks:
        text += f"\n🆔 #{task['id']} - {task['title']} (+{task['reward']} Points)"
        buttons.append([InlineKeyboardButton(f"❌ Delete #{task['id']} - {task['title']}", callback_data=f"del_task_{task['id']}")])

    buttons.append([InlineKeyboardButton("➕ Add New Task", callback_data="add_task_start")])
    buttons.append([InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_home")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def delete_task_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    task_id = int(query.data.split("_")[2])
    conn = db()
    conn.execute("UPDATE channel_tasks SET active=0 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"✅ Task #{task_id} মুছে ফেলা হয়েছে।")


async def add_task_start(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    context.user_data["add_task_state"] = "title"
    await query.message.reply_text("➕ ADD NEW TASK\n\nটাস্কের Title/নাম পাঠাও:\nExample: Join AMIR Channel")


async def process_add_task(update, context):
    if not is_admin(update.effective_user.id):
        return False

    state = context.user_data.get("add_task_state")
    if not state:
        return False

    text = update.message.text.strip()

    if state == "title":
        context.user_data["new_task_title"] = text
        context.user_data["add_task_state"] = "channel"
        await update.message.reply_text("📢 Channel Username (বা ID) পাঠাও:\nExample: @Amir10m300")
        return True

    elif state == "channel":
        context.user_data["new_task_channel"] = text
        context.user_data["add_task_state"] = "url"
        await update.message.reply_text("🔗 Channel Invite URL পাঠাও:\nExample: https://t.me/Amir10m300")
        return True

    elif state == "url":
        context.user_data["new_task_url"] = text
        context.user_data["add_task_state"] = "reward"
        await update.message.reply_text("🎁 Task Reward Points পাঠাও:\nExample: 10")
        return True

    elif state == "reward":
        try:
            reward = int(text)
        except ValueError:
            await update.message.reply_text("❌ Reward পয়েন্ট শুধু সংখ্যায় পাঠাও।")
            return True

        title = context.user_data.get("new_task_title")
        channel = context.user_data.get("new_task_channel")
        url = context.user_data.get("new_task_url")

        conn = db()
        conn.execute(
            "INSERT INTO channel_tasks(title, channel, channel_url, reward, active) VALUES(?,?,?,?,1)",
            (title, channel, url, reward)
        )
        conn.commit()
        conn.close()

        context.user_data.pop("add_task_state", None)
        await update.message.reply_text("🎉 নতুন টাস্ক সফলভাবে যুক্ত করা হয়েছে!", reply_markup=get_markup())
        return True

    return False


# =========================
# BROADCAST
# =========================

async def broadcast_start(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    context.user_data["broadcast"] = True
    await query.message.reply_text("📢 BROADCAST\n\nযে message সবাইকে পাঠাতে চাও সেটা এখন পাঠাও。\n\n❌ Cancel করতে /cancel পাঠাও।")


async def process_broadcast(update, context):
    if not is_admin(update.effective_user.id) or not context.user_data.get("broadcast"):
        return False

    text = update.message.text
    if text == "/cancel":
        context.user_data.pop("broadcast", None)
        await update.message.reply_text("❌ Broadcast cancelled।", reply_markup=get_markup())
        return True

    conn = db()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    sent, failed = 0, 0
    for row in users:
        try:
            await context.bot.send_message(chat_id=row["user_id"], text=f"📢 ANNOUNCEMENT\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    context.user_data.pop("broadcast", None)
    await update.message.reply_text(f"📢 BROADCAST COMPLETE\n\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=get_markup())
    return True
    
