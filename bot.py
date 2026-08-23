import os
import threading
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

ADMIN_ID = 7003609983

REQUIRED_CHANNEL = "@TaskMint_v1"
CHANNEL_LINK = "https://t.me/TaskMint_v1"

TOKEN_NAME = "POL"
DEFAULT_MIN_WITHDRAW = 1.0
DEFAULT_REF_COMMISSION = 0.5
DATABASE_NAME = "taskmint"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is missing.")


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server started on port {port}")
    server.serve_forever()
    
mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]
submissions_collection = db["task_submissions"]
settings_collection = db["settings"]
def setup_database():
    users_collection.create_index("user_id", unique=True)
    tasks_collection.create_index("task_id", unique=True)
    withdrawals_collection.create_index("user_id")
    withdrawals_collection.create_index("status")
    submissions_collection.create_index("user_id")
    submissions_collection.create_index("task_id")
    settings_collection.create_index("key", unique=True)

    print("MongoDB database ready.")


def get_setting(key, default_value):
    setting = settings_collection.find_one({"key": key})
    if setting:
        return setting.get("value", default_value)
    return default_value


def update_setting(key, value):
    settings_collection.update_one(
        {"key": key},
        {"$set": {"value": value}},
        upsert=True
    )


def create_or_update_user(user, referrer_id=None):
    now = datetime.now(timezone.utc)
    existing_user = users_collection.find_one({"user_id": user.id})

    if not existing_user:
        ref_by = None
        if referrer_id and referrer_id != user.id:
            ref_user = users_collection.find_one({"user_id": referrer_id})
            if ref_user:
                ref_by = referrer_id
                users_collection.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"referrals": 1}}
                )

        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                },
                "$setOnInsert": {
                    "user_id": user.id,
                    "balance": 0.0,
                    "referrals": 0,
                    "referred_by": ref_by,
                    "ref_bonus_paid": False,
                    "completed_tasks": [],
                    "is_banned": False,
                    "created_at": now
                }
            },
            upsert=True
        )
    else:
        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                }
            }
        )


def get_user(user_id):
    return users_collection.find_one({"user_id": user_id})


def get_balance(user_id):
    user = get_user(user_id)
    if not user:
        return 0.0
    return float(user.get("balance", 0.0))
        
