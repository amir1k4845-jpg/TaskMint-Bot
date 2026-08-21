import os
import re
import asyncio
import threading
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


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

MONGO_URI = os.getenv("MONGO_URI")

PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

OFFICIAL_CHANNEL = "@TaskMint_v1"
OFFICIAL_CHANNEL_URL = "https://t.me/TaskMint_v1"

# POL rewards
TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100


# =========================================================
# CONVERSATION STATES
# =========================================================

(
    AMOUNT,
    METHOD,
    ACCOUNT,
) = range(3)


# =========================================================
# MONGODB
# =========================================================

if not MONGO_URI:
    raise RuntimeError("MONGO_URI environment variable is missing.")


client = pymongo.MongoClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=True,
    serverSelectionTimeoutMS=10000,
)

db = client["taskmint_bot_db"]

users_col = db["users"]
completed_tasks_col = db["completed_tasks"]
withdrawals_col = db["withdrawals"]
channel_tasks_col = db["channel_tasks"]
settings_col = db["settings"]
transactions_col = db["transactions"]


# =========================================================
# DEFAULT SETTINGS
# =========================================================

DEFAULT_SETTINGS = {
    "button_earn": "💰 Earn Tasks",
    "button_referral": "👥 Refer & Earn",
    "button_withdraw": "💳 Withdraw POL",
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


BUTTON_KEYS = [
    ("earn", "Earn Tasks"),
    ("referral", "Refer & Earn"),
    ("withdraw", "Withdraw POL"),
    ("daily", "Daily Bonus"),
    ("balance", "My Balance"),
    ("help", "Help"),
]


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def init_db():
    """
    Initialize settings and MongoDB indexes.
    """

    for key, value in DEFAULT_SETTINGS.items():

        settings_col.update_one(
            {"key": key},
            {
                "$setOnInsert": {
                    "key": key,
                    "value": value,
                }
            },
            upsert=True,
        )

    # Unique user ID
    try:
        users_col.create_index(
            [("user_id", pymongo.ASCENDING)],
            unique=True,
        )
    except Exception:
        pass

    # Prevent duplicate task completion
    try:
        completed_tasks_col.create_index(
            [
                ("user_id", pymongo.ASCENDING),
                ("task_key", pymongo.ASCENDING),
            ],
            unique=True,
        )
    except Exception:
        pass

    # Withdrawal request lookup
    try:
        withdrawals_col.create_index(
            [("req_id", pymongo.ASCENDING)],
            unique=True,
        )
    except Exception:
        pass

    try:
        withdrawals_col.create_index(
            [("user_id", pymongo.ASCENDING)]
        )
    except Exception:
        pass

    try:
        transactions_col.create_index(
            [("user_id", pymongo.ASCENDING)]
        )
    except Exception:
        pass


# =========================================================
# SETTINGS HELPERS
# =========================================================

def get_setting(key):

    row = settings_col.find_one(
        {"key": key}
    )

    if row:
        return row.get(
            "value",
            DEFAULT_SETTINGS.get(key, "")
        )

    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):

    settings_col.update_one(
        {"key": key},
        {
            "$set": {
                "value": str(value)
            }
        },
        upsert=True,
    )


def setting_int(key, fallback):

    try:
        return int(get_setting(key))

    except (
        TypeError,
        ValueError,
    ):
        return fallback


def feature_on(feature):

    return (
        get_setting(
            f"feature_{feature}"
        ) == "1"
    )


# =========================================================
# USER MENU
# =========================================================

def get_markup():

    rows = []
    current = []

    for key in (
        "earn",
        "referral",
        "withdraw",
        "daily",
        "balance",
        "help",
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
        resize_keyboard=True,
    )


# =========================================================
# ADMIN MENU
# =========================================================

def get_admin_markup():

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            ),
        ],
        [
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            ),
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ Add POL",
                callback_data="admin_add_pol"
            ),
            InlineKeyboardButton(
                "➖ Remove POL",
                callback_data="admin_remove_pol"
            ),
        ],
        [
            InlineKeyboardButton(
                "🚫 Ban User",
                callback_data="admin_ban"
            ),
            InlineKeyboardButton(
                "✅ Unban User",
                callback_data="admin_unban"
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Tasks",
                callback_data="admin_tasks"
            ),
            InlineKeyboardButton(
                "⚙️ Settings",
                callback_data="admin_settings"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id):

    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


# =========================================================
# USER REGISTRATION
# =========================================================

def register_user(
    user,
    referred_by=None,
):

    existing = users_col.find_one(
        {"user_id": user.id}
    )

    if not existing:

        users_col.insert_one(
            {
                "user_id": user.id,
                "username": user.username or "",
                "first_name": user.first_name or "",
                "points": 0,
                "referred_by": referred_by,
                "referral_rewarded": 0,
                "last_bonus": "",
                "banned": 0,
                "created_at": datetime.utcnow(),
            }
        )

    else:

        users_col.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                }
            },
        )


# =========================================================
# USER HELPERS
# =========================================================

def get_user(user_id):

    return users_col.find_one(
        {"user_id": user_id}
    )


def points(user_id):

    user = get_user(user_id)

    if not user:
        return 0

    return int(
        user.get("points", 0)
    )


def is_banned(user_id):

    user = get_user(user_id)

    if not user:
        return False

    return (
        user.get("banned", 0) == 1
    )


# =========================================================
# TRANSACTION HISTORY
# =========================================================

def add_transaction(
    user_id,
    amount,
    transaction_type,
    description="",
):

    transactions_col.insert_one(
        {
            "user_id": user_id,
            "amount": amount,
            "type": transaction_type,
            "description": description,
            "created_at": datetime.utcnow(),
        }
    )


# =========================================================
# ATOMIC BALANCE OPERATIONS
# =========================================================

def add_points(
    user_id,
    amount,
    transaction_type="credit",
    description="",
):

    if amount <= 0:
        return False

    result = users_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "points": amount
            }
        },
    )

    if result.modified_count:

        add_transaction(
            user_id,
            amount,
            transaction_type,
            description,
        )

        return True

    return False


def remove_points(
    user_id,
    amount,
    transaction_type="debit",
    description="",
):

    if amount <= 0:
        return False

    result = users_col.update_one(
        {
            "user_id": user_id,
            "points": {
                "$gte": amount
            },
        },
        {
            "$inc": {
                "points": -amount
            }
        },
    )

    if result.modified_count:

        add_transaction(
            user_id,
            -amount,
            transaction_type,
            description,
        )

        return True

    return False


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    referred_by = None

    if context.args:

        try:

            ref_id = int(
                context.args[0]
            )

            if (
                ref_id != user.id
                and get_user(ref_id)
            ):
                referred_by = ref_id

        except ValueError:

            referred_by = None

    existing = get_user(
        user.id
    )

    register_user(
        user,
        referred_by
        if not existing
        else None,
    )

    if referred_by and not existing:

        await process_referral(
            user.id
        )

    if is_banned(user.id):

        await update.message.reply_text(
            "🚫 Your account is currently banned."
        )
        return

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn POL.\n"
        "👥 Invite friends and earn POL rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Withdraw your POL rewards.\n\n"
        "📢 Official Channel:\n"
        f"{OFFICIAL_CHANNEL_URL}\n\n"
        "👇 Select an option from the menu.",
        reply_markup=get_markup(),
    )


# =========================================================
# CREATE DEFAULT OFFICIAL TASK
# =========================================================

def create_default_task():

    existing = channel_tasks_col.find_one(
        {"task_id": 1}
    )

    if not existing:

        channel_tasks_col.insert_one(
            {
                "task_id": 1,
                "title": "Join Official Channel",
                "channel": OFFICIAL_CHANNEL,
                "channel_url": OFFICIAL_CHANNEL_URL,
                "reward": setting_int(
                    "reward_task",
                    TASK_REWARD,
                ),
                "active": 1,
                "created_at": datetime.utcnow(),
            }
        )


# =========================================================
# TASK HELPERS
# =========================================================

def get_channel_tasks():

    return list(
        channel_tasks_col.find(
            {"active": 1}
        ).sort(
            "task_id",
            pymongo.ASCENDING,
        )
    )


def get_channel_task(task_id):

    return channel_tasks_col.find_one(
        {
            "task_id": task_id,
            "active": 1,
        }
    )


def task_done(
    user_id,
    task_key,
):

    return (
        completed_tasks_col.find_one(
            {
                "user_id": user_id,
                "task_key": task_key,
            }
        )
        is not None
    )


def save_task(
    user_id,
    task_key,
):

    completed_tasks_col.update_one(
        {
            "user_id": user_id,
            "task_key": task_key,
        },
        {
            "$set": {
                "user_id": user_id,
                "task_key": task_key,
                "completed_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )


# =========================================================
# EARN TASKS
# =========================================================

async def earn_tasks(
    update,
    context,
):

    if is_banned(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "🚫 Your account is banned."
        )
        return

    if not feature_on("earn"):

        await update.message.reply_text(
            "⚠️ Earn Tasks is currently disabled.",
            reply_markup=get_markup(),
        )
        return

    rows = get_channel_tasks()

    if not rows:

        await update.message.reply_text(
            "💰 EARN TASKS\n\n"
            "There are currently no available tasks.",
            reply_markup=get_markup(),
        )
        return

    buttons = []

    for row in rows:

        task_id = row["task_id"]

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📢 {row['title']} "
                    f"(+{row['reward']} POL)",
                    url=row["channel_url"],
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ Check Task #{task_id}",
                    callback_data=f"check_task_{task_id}",
                )
            ]
        )

    await update.message.reply_text(
        "💰 EARN TASKS\n\n"
        "📌 How to complete a task:\n"
        "1. Join the required channel.\n"
        "2. Return here and press Check Task.\n"
        "3. Receive your POL reward.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


# =========================================================
# TASK CALLBACK
# =========================================================

async def task_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if is_banned(user_id):

        await query.edit_message_text(
            "🚫 Your account is banned."
        )
        return

    data = query.data

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
        IndexError,
    ):

        await query.answer(
            "Invalid task.",
            show_alert=True,
        )
        return

    task = get_channel_task(
        task_id
    )

    if not task:

        await query.edit_message_text(
            "❌ This task is no longer available."
        )
        return

    task_key = f"channel_{task_id}"

    if task_done(
        user_id,
        task_key,
    ):

        await query.edit_message_text(
            "⚠️ TASK ALREADY COMPLETED\n\n"
            f"💰 Current Balance: "
            f"{points(user_id)} POL"
        )
        return

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel"],
            user_id=user_id,
        )

        if member.status in (
            "member",
            "administrator",
            "creator",
        ):

            reward = int(
                task["reward"]
            )

            add_points(
                user_id,
                reward,
                "task_reward",
                f"Completed task #{task_id}",
            )

            save_task(
                user_id,
                task_key,
            )

            await query.edit_message_text(
                "🎉 TASK COMPLETED!\n\n"
                f"✅ Reward: +{reward} POL\n"
                f"💰 Total Balance: "
                f"{points(user_id)} POL"
            )

        else:

            await query.edit_message_text(
                "❌ TASK NOT COMPLETED\n\n"
                "Please join the channel first."
            )

    except Exception as e:

        print(
            "Task verification error:",
            e,
        )

        await query.edit_message_text(
            "⚠️ Task verification failed.\n\n"
            "Please try again later."
    )
