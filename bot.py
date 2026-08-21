import os
import threading
import re
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import pymongo

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
MONGO_URI = os.getenv("MONGO_URI", "YOUR_MONGODB_URI_HERE")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100

AMOUNT, METHOD, ACCOUNT = range(3)

# =========================
# MONGODB DATABASE SETUP
# =========================

client = pymongo.MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client["taskmint_bot_db"]

users_col = db["users"]
completed_tasks_col = db["completed_tasks"]
withdrawals_col = db["withdrawals"]
channel_tasks_col = db["channel_tasks"]
settings_col = db["settings"]

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

def init_db():
    for key, value in DEFAULT_SETTINGS.items():
        if not settings_col.find_one({"key": key}):
            settings_col.insert_one({"key": key, "value": value})

def get_setting(key):
    row = settings_col.find_one({"key": key})
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

def set_setting(key, value):
    settings_col.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)

def setting_int(key, fallback):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return fallback

def feature_on(feature):
    return get_setting(f"feature_{feature}") == "1"
    
# =========================
# DYNAMIC MENU & USER HELPERS
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

def register_user(user, referred_by=None):
    existing = users_col.find_one({"user_id": user.id})
    if not existing:
        users_col.insert_one({
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "points": 0,
            "referred_by": referred_by,
            "referral_rewarded": 0,
            "last_bonus": ""
        })
    else:
        users_col.update_one(
            {"user_id": user.id},
            {"$set": {"username": user.username or "", "first_name": user.first_name or ""}}
        )

def get_user(user_id):
    return users_col.find_one({"user_id": user_id})

def points(user_id):
    user = get_user(user_id)
    return user["points"] if user else 0

def add_points(user_id, amount):
    users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})

def remove_points(user_id, amount):
    user = get_user(user_id)
    if user:
        new_p = max(user["points"] - amount, 0)
        users_col.update_one({"user_id": user_id}, {"$set": {"points": new_p}})

def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID

# =========================
# COMMANDS & FEATURES
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

def create_default_task():
    if channel_tasks_col.count_documents({}) == 0:
        channel_tasks_col.insert_one({
            "task_id": 1,
            "title": "Join Official Channel",
            "channel": "@Telegram",
            "channel_url": "https://t.me/Telegram",
            "reward": setting_int("reward_task", TASK_REWARD),
            "active": 1
        })

def get_channel_tasks():
    return list(channel_tasks_col.find({"active": 1}))

def get_channel_task(task_id):
    return channel_tasks_col.find_one({"task_id": task_id, "active": 1})

def task_done(user_id, task_key):
    return completed_tasks_col.find_one({"user_id": user_id, "task_key": task_key}) is not None

def save_task(user_id, task_key):
    completed_tasks_col.update_one(
        {"user_id": user_id, "task_key": task_key},
        {"$set": {"user_id": user_id, "task_key": task_key}},
        upsert=True
    )

async def earn_tasks(update, context):
    if not feature_on("earn"):
        await update.message.reply_text("⚠️ Earn Tasks feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    rows = get_channel_tasks()
    if not rows:
        await update.message.reply_text("💰 EARN TASKS\n\n😔 বর্তমানে কোনো Task available নেই।", reply_markup=get_markup())
        return

    buttons = []
    for row in rows:
        t_id = row["task_id"]
        buttons.append([InlineKeyboardButton(f"📢 {row['title']} (+{row['reward']} Points)", url=row["channel_url"])])
        buttons.append([InlineKeyboardButton(f"✅ Check Task #{t_id}", callback_data=f"check_task_{t_id}")])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n📌 Task complete করার নিয়ম:\n1️⃣ প্রথমে Channel Join করো।\n2️⃣ Join করার পর নিচের ✅ Check button চাপো।",
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
        await query.edit_message_text(f"⚠️ TASK ALREADY COMPLETED\n\n💰 Current Points: {points(user_id)}")
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel"], user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            add_points(user_id, task["reward"])
            save_task(user_id, task_key)
            await query.edit_message_text(f"🎉 TASK COMPLETED!\n\n✅ Reward: +{task['reward']} Points\n💰 Total Points: {points(user_id)}")
        else:
            await query.edit_message_text("❌ TASK NOT COMPLETED\n\nআগে Channel-এ Join করো।")
    except Exception as e:
        print("Task check error:", e)
        await query.edit_message_text("⚠️ Task verify করা যাচ্ছে না।")
