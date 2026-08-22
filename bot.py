import os
import threading
import re
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from motor.motor_asyncio import AsyncIOMotorClient

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

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGO_URI = os.getenv("MONGO_URI", "YOUR_MONGODB_URI_HERE")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

# Default crypto rewards (POL token)
TASK_REWARD = 0.5
REFERRAL_REWARD = 1.0
DAILY_REWARD = 0.1
MIN_WITHDRAW = 5.0

AMOUNT, METHOD, ACCOUNT = range(3)

# =========================
# MONGODB ASYNC SETUP
# =========================

client = AsyncIOMotorClient(MONGO_URI, tls=True)
db = client["taskmint_bot_db"]

users_col = db["users"]
completed_tasks_col = db["completed_tasks"]
withdrawals_col = db["withdrawals"]
channel_tasks_col = db["channel_tasks"]
settings_col = db["settings"]

DEFAULT_SETTINGS = {
    "button_earn": "💰 Earn POL",
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
    "reward_task": str(TASK_REWARD),
    "reward_referral": str(REFERRAL_REWARD),
    "reward_daily": str(DAILY_REWARD),
    "min_withdraw": str(MIN_WITHDRAW),
}

SETTINGS_CACHE = {}

async def load_settings():
    global SETTINGS_CACHE
    async for setting in settings_col.find({}):
        SETTINGS_CACHE[setting["key"]] = setting["value"]
    
    for key, value in DEFAULT_SETTINGS.items():
        if key not in SETTINGS_CACHE:
            SETTINGS_CACHE[key] = value
            await settings_col.insert_one({"key": key, "value": value})

def get_setting(key):
    return SETTINGS_CACHE.get(key, DEFAULT_SETTINGS.get(key, ""))

def setting_float(key, fallback):
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        return fallback

def feature_on(feature):
    return get_setting(f"feature_{feature}") == "1"

# =========================
# USER HELPERS & MENUS
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

def get_menu_buttons_list():
    return [get_setting(f"button_{k}") for k in ("earn", "referral", "withdraw", "daily", "balance", "help")]

async def register_user(user, referred_by=None):
    existing = await users_col.find_one({"user_id": user.id})
    if not existing:
        await users_col.insert_one({
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "balance": 0.0,
            "referred_by": referred_by,
            "referral_rewarded": 0,
            "last_bonus": ""
        })

async def get_user(user_id):
    return await users_col.find_one({"user_id": user_id})

async def get_balance(user_id):
    user = await get_user(user_id)
    return float(user.get("balance", 0.0)) if user else 0.0

async def add_balance(user_id, amount):
    await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": float(amount)}})

async def remove_balance(user_id, amount):
    user = await get_user(user_id)
    if user:
        new_b = max(float(user.get("balance", 0.0)) - float(amount), 0.0)
        await users_col.update_one({"user_id": user_id}, {"$set": {"balance": new_b}})

async def create_default_task():
    count = await channel_tasks_col.count_documents({})
    if count == 0:
        await channel_tasks_col.insert_one({
            "task_id": 1,
            "title": "Join Official Channel",
            "channel": "@Telegram",
            "channel_url": "https://t.me/Telegram",
            "reward": setting_float("reward_task", TASK_REWARD),
            "active": 1
        })
        
