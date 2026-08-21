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
    
