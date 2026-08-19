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

ADMIN_ID = 7003609983

DB_FILE = "taskmint.db"

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100

AMOUNT, METHOD, ACCOUNT = range(3)

MENU = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]

MARKUP = ReplyKeyboardMarkup(
    MENU,
    resize_keyboard=True
)

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


def get_setting(key):
    conn = db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()
    conn.close()
    if row is not None:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
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
    CREATE TABLE IF NOT EXISTS tasks(
        user_id INTEGER,
        task_id TEXT,
        PRIMARY KEY(user_id, task_id)
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
        status TEXT DEFAULT 'pending'
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS channel_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        channel_url TEXT NOT NULL,
        title TEXT NOT NULL,
        reward INTEGER DEFAULT 10,
        active INTEGER DEFAULT 1
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
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
# USER REGISTER
# =========================

def register_user(user, referrer=None):

    conn = db()

    old = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user.id,)
    ).fetchone()

    if not old:

        valid_referrer = None

        if referrer and referrer != user.id:

            exists = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (referrer,)
            ).fetchone()

            if exists:
                valid_referrer = referrer

        conn.execute("""
        INSERT INTO users
        (
            user_id,
            username,
            first_name,
            referred_by
        )
        VALUES (?, ?, ?, ?)
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            valid_referrer
        ))

        if valid_referrer:

            conn.execute("""
            UPDATE users
            SET points = points + ?,
                referral_rewarded = 1
            WHERE user_id = ?
            """, (
                setting_int("reward_referral", REFERRAL_REWARD),
                valid_referrer
            ))

    else:

        conn.execute("""
        UPDATE users
        SET username=?,
            first_name=?
        WHERE user_id=?
        """, (
            user.username or "",
            user.first_name or "",
            user.id
        ))

    conn.commit()
    conn.close()


def points(user_id):

    conn = db()

    row = conn.execute(
        "SELECT points FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    conn.close()

    if row:
        return row["points"]

    return 0


def add_points(user_id, amount):

    conn = db()

    conn.execute("""
    UPDATE users
    SET points = points + ?
    WHERE user_id=?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


def remove_points(user_id, amount):

    conn = db()

    conn.execute("""
    UPDATE users
    SET points = MAX(points - ?, 0)
    WHERE user_id=?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


# =========================
# USER INFORMATION
# =========================

def get_user_info(user_id):

    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not user:

        conn.close()

        return None

    referrals = conn.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by=?",
        (user_id,)
    ).fetchone()[0]

    completed_tasks = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id=?",
        (user_id,)
    ).fetchone()[0]

    conn.close()

    return {
        "user": user,
        "referrals": referrals,
        "tasks": completed_tasks
    }


def admin_add_points(user_id, amount):

    conn = db()

    row = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row:

        conn.close()

        return False

    conn.execute("""
    UPDATE users
    SET points = points + ?
    WHERE user_id=?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()

    return True


def admin_remove_points(user_id, amount):

    conn = db()

    row = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row:

        conn.close()

        return False

    conn.execute("""
    UPDATE users
    SET points = MAX(points - ?, 0)
    WHERE user_id=?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()

    return True


# =========================
# TASK DATABASE
# =========================

def task_done(user_id, task_id):

    conn = db()

    row = conn.execute("""
    SELECT 1
    FROM tasks
    WHERE user_id=? AND task_id=?
    """, (
        user_id,
        str(task_id)
    )).fetchone()

    conn.close()

    return row is not None


def save_task(user_id, task_id):

    conn = db()

    conn.execute("""
    INSERT OR IGNORE INTO tasks
    (user_id, task_id)
    VALUES (?, ?)
    """, (
        user_id,
        str(task_id)
    ))

    conn.commit()
    conn.close()


def get_channel_tasks():

    conn = db()

    rows = conn.execute("""
    SELECT *
    FROM channel_tasks
    WHERE active=1
    ORDER BY id DESC
    """).fetchall()

    conn.close()

    return rows


def get_channel_task(task_id):

    conn = db()

    row = conn.execute("""
    SELECT *
    FROM channel_tasks
    WHERE id=? AND active=1
    """, (
        task_id,
    )).fetchone()

    conn.close()

    return row


def add_channel_task(
    channel,
    channel_url,
    title,
    reward
):

    conn = db()

    conn.execute("""
    INSERT INTO channel_tasks
    (
        channel,
        channel_url,
        title,
        reward,
        active
    )
    VALUES (?, ?, ?, ?, 1)
    """, (
        channel,
        channel_url,
        title,
        reward
    ))

    conn.commit()
    conn.close()


def delete_channel_task(task_id):

    conn = db()

    conn.execute("""
    UPDATE channel_tasks
    SET active=0
    WHERE id=?
    """, (
        task_id,
    ))

    conn.commit()
    conn.close()


# =========================
# DEFAULT TASK
# =========================

def create_default_task():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM channel_tasks"
    ).fetchone()[0]

    if count == 0:

        conn.execute("""
        INSERT INTO channel_tasks
        (
            channel,
            channel_url,
            title,
            reward,
            active
        )
        VALUES (?, ?, ?, ?, 1)
        """, (
            "@Amir10m300",
            "https://t.me/Amir10m300",
            "📢 Join Channel",
            setting_int("reward_task", TASK_REWARD)
        ))

    conn.commit()
    conn.close()


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"TaskMint Bot is running!"
        )

    def log_message(
        self,
        format,
        *args
    ):

        pass


def web_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    server.serve_forever()


# =========================
# START
# =========================

async def start(update, context):

    user = update.effective_user

    referrer = None

    if context.args:

        try:

            referrer = int(
                context.args[0]
            )

        except (
            ValueError,
            TypeError
        ):

            referrer = None

    register_user(
        user,
        referrer
    )

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করো।\n"
        "👥 Friends invite করে Points earn করো।\n"
        "🎁 Daily Bonus নাও।\n"
        "💳 Points withdraw করো।\n\n"
        "নিচের Menu থেকে শুরু করো 👇",
        reply_markup=get_markup()
    )


# =========================
# HELP
# =========================
async def help_message(
    update,
    context
):

    await update.message.reply_text(
        "ℹ️ TASKMINT HELP\n\n"
        "💰 Earn Tasks — Tasks complete করে points earn করো।\n"
        "👥 Refer & Earn — বন্ধু invite করে points earn করো।\n"
        "💳 Withdraw — তোমার points withdraw করো।\n"
        "🎁 Daily Bonus — প্রতিদিন bonus নাও।\n"
        "📊 My Balance — তোমার বর্তমান balance দেখো।\n\n"
        "কোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।",
        reply_markup=get_markup()
    )


# =========================
# BALANCE
# =========================

async def balance_message(
    update,
    context
):

    user_id = update.effective_user.id

    info = get_user_info(user_id)

    if not info:

        register_user(
            update.effective_user
        )

        info = get_user_info(
            user_id
        )

    user = info["user"]

    await update.message.reply_text(
        "📊 MY BALANCE\n\n"
        f"👤 User ID: {user_id}\n"
        f"💰 Points: {user['points']}\n"
        f"👥 Referrals: {info['referrals']}\n"
        f"📋 Completed Tasks: {info['tasks']}",
        reply_markup=get_markup()
    )


# =========================
# REFERRAL
# =========================

async def referral_message(
    update,
    context
):

    user_id = update.effective_user.id

    bot_username = context.bot.username

    if not bot_username:

        bot_username = "TaskMintBot"

    link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    reward = setting_int(
        "reward_referral",
        REFERRAL_REWARD
    )

    info = get_user_info(
        user_id
    )

    referrals = 0

    if info:

        referrals = info["referrals"]

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        f"💰 প্রতি referral: +{reward} Points\n"
        f"👤 Total Referrals: {referrals}\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{link}\n\n"
        "বন্ধুদের এই link share করো।",
        reply_markup=get_markup()
    )


# =========================
# DAILY BONUS
# =========================

async def daily_bonus_message(
    update,
    context
):

    user_id = update.effective_user.id

    conn = db()

    row = conn.execute("""
    SELECT last_bonus
    FROM users
    WHERE user_id=?
    """, (
        user_id,
    )).fetchone()

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    if row and row["last_bonus"] == today:

        conn.close()

        await update.message.reply_text(
            "⏳ তুমি আজকের Daily Bonus already নিয়েছো।\n\n"
            "আগামীকাল আবার bonus নিতে পারবে।",
            reply_markup=get_markup()
        )

        return

    reward = setting_int(
        "reward_daily",
        DAILY_REWARD
    )

    conn.execute("""
    UPDATE users
    SET points = points + ?,
        last_bonus = ?
    WHERE user_id=?
    """, (
        reward,
        today,
        user_id
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎁 DAILY BONUS\n\n"
        f"✅ +{reward} Points added!\n\n"
        f"💰 Current Balance: {points(user_id)} Points",
        reply_markup=get_markup()
    )


# =========================
# EARN TASKS
# =========================

async def earn_tasks_message(
    update,
    context
):

    user_id = update.effective_user.id

    task_rows = get_channel_tasks()

    if not task_rows:

        await update.message.reply_text(
            "📋 এখন কোনো task available নেই।\n\n"
            "পরে আবার চেষ্টা করো।",
            reply_markup=get_markup()
        )

        return

    buttons = []

    text = (
        "💰 EARN TASKS\n\n"
        "Channel join করে task complete করো।\n\n"
    )

    available = 0

    for task in task_rows:

        if task_done(
            user_id,
            task["id"]
        ):
            continue

        available += 1

        text += (
            f"📌 {task['title']}\n"
            f"💰 Reward: +{task['reward']} Points\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                "📢 Join Channel",
                url=task["channel_url"]
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                f"✅ Verify +{task['reward']}",
                callback_data=(
                    f"verify_task_{task['id']}"
                )
            )
        ])

    if available == 0:

        await update.message.reply_text(
            "🎉 তুমি সব available task complete করে ফেলেছো!",
            reply_markup=get_markup()
        )

        return

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================
# TASK VERIFY
# =========================

async def verify_task_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    try:

        task_id = int(
            query.data.replace(
                "verify_task_",
                ""
            )
        )

    except (
        ValueError,
        TypeError
    ):

        await query.answer(
            "❌ Invalid task.",
            show_alert=True
        )

        return

    task = get_channel_task(
        task_id
    )

    if not task:

        await query.answer(
            "❌ Task পাওয়া যায়নি।",
            show_alert=True
        )

        return

    if task_done(
        user_id,
        task_id
    ):

        await query.answer(
            "✅ এই task already completed.",
            show_alert=True
        )

        return

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel"],
            user_id=user_id
        )

        if member.status not in (
            "member",
            "administrator",
            "creator"
        ):

            await query.answer(
                "❌ আগে channel-এ join করো।",
                show_alert=True
            )

            return

    except Exception as e:

        print(
            "Task verification error:",
            e
        )

        await query.answer(
            "❌ Verification failed.\n"
            "Channel join করা আছে কিনা check করো।",
            show_alert=True
        )

        return

    save_task(
        user_id,
        task_id
    )

    add_points(
        user_id,
        task["reward"]
    )

    await query.answer(
        f"🎉 +{task['reward']} Points added!",
        show_alert=True
    )

    try:

        await query.edit_message_text(
            "🎉 TASK COMPLETED!\n\n"
            f"📌 {task['title']}\n"
            f"💰 Reward: +{task['reward']} Points\n\n"
            f"📊 Balance: {points(user_id)} Points"
        )

    except Exception:

        pass


# =========================
# WITHDRAW
# =========================

async def withdraw_start(
    update,
    context
):

    user_id = update.effective_user.id

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    balance = points(
        user_id
    )

    if balance < minimum:

        await update.message.reply_text(
            "❌ Withdraw করার জন্য পর্যাপ্ত Points নেই।\n\n"
            f"💰 Your Balance: {balance}\n"
            f"💳 Minimum Withdraw: {minimum}",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"💰 Current Balance: {balance} Points\n"
        f"📌 Minimum Withdraw: {minimum} Points\n\n"
        "কত Points withdraw করতে চাও?\n\n"
        "Example: 100\n\n"
        "Cancel করতে /cancel লিখো।"
    )

    return AMOUNT


async def withdraw_amount(
    update,
    context
):

    text = update.message.text.strip()

    try:

        amount = int(text)

    except (
        ValueError,
        TypeError
    ):

        await update.message.reply_text(
            "❌ সঠিক amount লিখো।\n\n"
            "Example: 100"
        )

        return AMOUNT

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    balance = points(
        update.effective_user.id
    )

    if amount < minimum:

        await update.message.reply_text(
            f"❌ Minimum withdraw {minimum} Points."
        )

        return AMOUNT

    if amount > balance:

        await update.message.reply_text(
            f"❌ তোমার balance মাত্র {balance} Points."
        )

        return AMOUNT

    context.user_data[
        "withdraw_amount"
    ] = amount

    await update.message.reply_text(
        "💳 Payment Method লিখো।\n\n"
        "Example:\n"
        "Binance"
    )

    return METHOD


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
        "📱 Payment Account / Address পাঠাও।\n\n"
        "Example:\n"
        "123456789"
    )

    return ACCOUNT


async def withdraw_account(
    update,
    context
):

    account = update.message.text.strip()

    if not account:

        await update.message.reply_text(
            "❌ Account / Address পাঠাও।"
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
            "❌ Withdrawal session expired।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    balance = points(
        user.id
    )

    if amount > balance:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ তোমার balance পরিবর্তন হয়েছে।\n"
            "আবার withdrawal request করো।",
            reply_markup=get_markup()
        )

        return ConversationHandler.END

    remove_points(
        user.id,
        amount
    )

    conn = db()

    cursor = conn.execute("""
    INSERT INTO withdrawals
    (
        user_id,
        username,
        amount,
        method,
        account,
        status
    )
    VALUES (?, ?, ?, ?, ?, 'pending')
    """, (
        user.id,
        user.username or "",
        amount,
        method,
        account
    ))

    withdrawal_id = cursor.lastrowid

    conn.commit()
    conn.close()

    context.user_data.clear()

    await update.message.reply_text(
        "✅ WITHDRAWAL REQUEST SUBMITTED\n\n"
        f"🆔 Request ID: #{withdrawal_id}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n\n"
        "⏳ Admin review করার পর payment করা হবে।",
        reply_markup=get_markup()
    )

    if ADMIN_ID:

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 NEW WITHDRAWAL\n\n"
                    f"🆔 Request: #{withdrawal_id}\n"
                    f"👤 User ID: {user.id}\n"
                    f"👤 Username: @{user.username or 'N/A'}\n"
                    f"💰 Amount: {amount} Points\n"
                    f"💳 Method: {method}\n"
                    f"📱 Account: {account}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=(
                                f"approve_withdraw_{withdrawal_id}"
                            )
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=(
                                f"reject_withdraw_{withdrawal_id}"
                            )
                        )
                    ]
                ])
            )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    return ConversationHandler.END


async def withdraw_cancel(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Withdrawal cancelled.",
        reply_markup=get_markup()
    )

    return ConversationHandler.END


# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id):

    return (
        int(user_id) == int(ADMIN_ID)
    )


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ You are not authorized."
        )

        return

    keyboard = [

        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 User Management",
                callback_data="admin_users"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],

        [
            InlineKeyboardButton(
                "💳 Pending Withdrawals",
                callback_data="admin_withdrawals"
            )
        ],

        [
            InlineKeyboardButton(
                "⚙️ Bot Customization",
                callback_data="admin_customize"
            )
        ],

        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ]
    ]

    await update.message.reply_text(
        "👑 ADMIN PANEL\n\n"
        "Bot control করার জন্য option select করো:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# ADMIN CUSTOMIZATION
# =========================

async def admin_customize(
    query
):

    keyboard = [

        [
            InlineKeyboardButton(
                "🔘 Button Settings",
                callback_data="custom_buttons"
            )
        ],

        [
            InlineKeyboardButton(
                "⚙️ Feature ON/OFF",
                callback_data="custom_features"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 Reward Settings",
                callback_data="custom_rewards"
            )
        ],

        [
            InlineKeyboardButton(
                "💳 Withdraw Settings",
                callback_data="custom_withdraw"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Admin Panel",
                callback_data="admin_back"
            )
        ]
    ]

    await query.edit_message_text(
        "⚙️ BOT CUSTOMIZATION\n\n"
        "এখান থেকে code edit না করেই "
        "Bot-এর button ও feature পরিবর্তন করতে পারবে।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# BUTTON SETTINGS
# =========================

async def custom_buttons(
    query
):

    button_names = {

        "earn":
            get_setting(
                "button_earn"
            ),

        "referral":
            get_setting(
                "button_referral"
            ),

        "withdraw":
            get_setting(
                "button_withdraw"
            ),

        "daily":
            get_setting(
                "button_daily"
            ),

        "balance":
            get_setting(
                "button_balance"
            ),

        "help":
            get_setting(
                "button_help"
            )
    }

    keyboard = []

    for key, name in button_names.items():

        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {name}",
                callback_data=(
                    f"rename_button_{key}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="admin_customize"
        )
    ])

    await query.edit_message_text(
        "🔘 BUTTON SETTINGS\n\n"
        "যে button-এর নাম change করতে চাও "
        "সেটাতে চাপো।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# FEATURE SETTINGS
# =========================

async def custom_features(
    query
):

    features = [

        ("earn", "💰 Earn Tasks"),
        ("referral", "👥 Refer & Earn"),
        ("withdraw", "💳 Withdraw"),
        ("daily", "🎁 Daily Bonus"),
        ("balance", "📊 My Balance"),
        ("help", "ℹ️ Help")
    ]

    keyboard = []

    for key, name in features:

        status = (
            "🟢 ON"
            if feature_on(key)
            else "🔴 OFF"
        )

        keyboard.append([
            InlineKeyboardButton(
                f"{name} — {status}",
                callback_data=(
                    f"toggle_feature_{key}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="admin_customize"
        )
    ])

    await query.edit_message_text(
        "⚙️ FEATURE ON/OFF\n\n"
        "Button চাপলে feature ON/OFF হবে।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# REWARD SETTINGS
# =========================

async def custom_rewards(
    query
):

    task_reward = setting_int(
        "reward_task",
        TASK_REWARD
    )

    referral_reward = setting_int(
        "reward_referral",
        REFERRAL_REWARD
    )

    daily_reward = setting_int(
        "reward_daily",
        DAILY_REWARD
    )

    keyboard = [

        [
            InlineKeyboardButton(
                f"📋 Task: {task_reward}",
                callback_data="set_reward_task"
            )
        ],

        [
            InlineKeyboardButton(
                f"👥 Referral: {referral_reward}",
                callback_data="set_reward_referral"
            )
        ],

        [
            InlineKeyboardButton(
                f"🎁 Daily: {daily_reward}",
                callback_data="set_reward_daily"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="admin_customize"
            )
        ]
    ]

    await query.edit_message_text(
        "💰 REWARD SETTINGS\n\n"
        "যে reward change করতে চাও সেটাতে চাপো।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# WITHDRAW SETTINGS
# =========================

async def custom_withdraw(
    query
):

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    keyboard = [

        [
            InlineKeyboardButton(
                f"💳 Minimum: {minimum}",
                callback_data="set_min_withdraw"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="admin_customize"
            )
        ]
    ]

    await query.edit_message_text(
        "💳 WITHDRAW SETTINGS\n\n"
        f"Current Minimum: {minimum} Points\n\n"
        "Minimum change করতে নিচের button চাপো।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
)
# =========================
# ADMIN CALLBACK HANDLER
# =========================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ Unauthorized",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data


    # =========================
    # ADMIN BACK
    # =========================

    if data == "admin_back":

        keyboard = [

            [
                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="admin_stats"
                )
            ],

            [
                InlineKeyboardButton(
                    "👥 User Management",
                    callback_data="admin_users"
                )
            ],

            [
                InlineKeyboardButton(
                    "📋 Manage Tasks",
                    callback_data="admin_tasks"
                )
            ],

            [
                InlineKeyboardButton(
                    "💳 Pending Withdrawals",
                    callback_data="admin_withdrawals"
                )
            ],

            [
                InlineKeyboardButton(
                    "⚙️ Bot Customization",
                    callback_data="admin_customize"
                )
            ],

            [
                InlineKeyboardButton(
                    "📢 Broadcast",
                    callback_data="admin_broadcast"
                )
            ]
        ]

        await query.edit_message_text(
            "👑 ADMIN PANEL\n\n"
            "Bot control করার জন্য option select করো:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return


    # =========================
    # CUSTOMIZATION
    # =========================

    if data == "admin_customize":

        await admin_customize(
            query
        )

        return


    # =========================
    # BUTTON SETTINGS
    # =========================

    if data == "custom_buttons":

        await custom_buttons(
            query
        )

        return


    # =========================
    # FEATURE SETTINGS
    # =========================

    if data == "custom_features":

        await custom_features(
            query
        )

        return


    # =========================
    # REWARD SETTINGS
    # =========================

    if data == "custom_rewards":

        await custom_rewards(
            query
        )

        return


    # =========================
    # WITHDRAW SETTINGS
    # =========================

    if data == "custom_withdraw":

        await custom_withdraw(
            query
        )

        return


    # =========================
    # TOGGLE FEATURE
    # =========================

    if data.startswith(
        "toggle_feature_"
    ):

        key = data.replace(
            "toggle_feature_",
            "",
            1
        )

        if key not in (
            "earn",
            "referral",
            "withdraw",
            "daily",
            "balance",
            "help"
        ):

            return

        current = feature_on(
            key
        )

        set_setting(
            f"feature_{key}",
            "0" if current else "1"
        )

        await custom_features(
            query
        )

        return


    # =========================
    # RENAME BUTTON
    # =========================

    if data.startswith(
        "rename_button_"
    ):

        key = data.replace(
            "rename_button_",
            "",
            1
        )

        if key not in (
            "earn",
            "referral",
            "withdraw",
            "daily",
            "balance",
            "help"
        ):

            return

        context.user_data[
            "admin_action"
        ] = f"rename_{key}"

        await query.message.reply_text(
            "✏️ BUTTON NAME CHANGE\n\n"
            f"Current Name:\n"
            f"{get_setting(f'button_{key}')}\n\n"
            "নতুন নাম পাঠাও।"
        )

        return


    # =========================
    # REWARD BUTTONS
    # =========================

    if data == "set_reward_task":

        context.user_data[
            "admin_action"
        ] = "reward_task"

        await query.message.reply_text(
            "📋 TASK REWARD\n\n"
            "নতুন reward amount পাঠাও।\n\n"
            "Example: 15"
        )

        return


    if data == "set_reward_referral":

        context.user_data[
            "admin_action"
        ] = "reward_referral"

        await query.message.reply_text(
            "👥 REFERRAL REWARD\n\n"
            "নতুন referral reward পাঠাও।\n\n"
            "Example: 25"
        )

        return


    if data == "set_reward_daily":

        context.user_data[
            "admin_action"
        ] = "reward_daily"

        await query.message.reply_text(
            "🎁 DAILY BONUS REWARD\n\n"
            "নতুন daily reward পাঠাও।\n\n"
            "Example: 15"
        )

        return


    # =========================
    # MINIMUM WITHDRAW
    # =========================

    if data == "set_min_withdraw":

        context.user_data[
            "admin_action"
        ] = "min_withdraw"

        await query.message.reply_text(
            "💳 MINIMUM WITHDRAW\n\n"
            "নতুন minimum amount পাঠাও।\n\n"
            "Example: 200"
        )

        return


    # =========================
    # STATISTICS
    # =========================

    if data == "admin_stats":

        conn = db()

        total_users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total_points = conn.execute(
            """
            SELECT COALESCE(
                SUM(points),
                0
            )
            FROM users
            """
        ).fetchone()[0]

        total_tasks = conn.execute(
            """
            SELECT COUNT(*)
            FROM channel_tasks
            WHERE active=1
            """
        ).fetchone()[0]

        total_withdrawals = conn.execute(
            """
            SELECT COUNT(*)
            FROM withdrawals
            """
        ).fetchone()[0]

        pending_withdrawals = conn.execute(
            """
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status='pending'
            """
        ).fetchone()[0]

        completed_tasks = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            """
        ).fetchone()[0]

        conn.close()

        await query.edit_message_text(
            "📊 TASKMINT STATISTICS\n\n"
            f"👥 Total Users: {total_users}\n"
            f"💰 Total Points: {total_points}\n"
            f"📋 Active Tasks: {total_tasks}\n"
            f"✅ Completed Tasks: {completed_tasks}\n"
            f"💳 Withdrawals: {total_withdrawals}\n"
            f"⏳ Pending: {pending_withdrawals}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Admin Panel",
                        callback_data="admin_back"
                    )
                ]
            ])
        )

        return


    # =========================
    # USER MANAGEMENT
    # =========================

    if data == "admin_users":

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM users
            ORDER BY rowid DESC
            LIMIT 30
            """
        ).fetchall()

        conn.close()

        if not rows:

            await query.edit_message_text(
                "👥 কোনো user পাওয়া যায়নি।"
            )

            return

        text = (
            "👥 USER MANAGEMENT\n\n"
        )

        buttons = []

        for row in rows:

            username = (
                f"@{row['username']}"
                if row["username"]
                else "No username"
            )

            text += (
                f"🆔 {row['user_id']}\n"
                f"👤 {username}\n"
                f"💰 {row['points']} Points\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"👤 {row['user_id']}",
                    callback_data=(
                        f"view_user_{row['user_id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Admin Panel",
                callback_data="admin_back"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return


    # =========================
    # VIEW USER
    # =========================

    if data.startswith(
        "view_user_"
    ):

        try:

            target_id = int(
                data.replace(
                    "view_user_",
                    "",
                    1
                )
            )

        except (
            ValueError,
            TypeError
        ):

            return

        info = get_user_info(
            target_id
        )

        if not info:

            await query.answer(
                "❌ User not found.",
                show_alert=True
            )

            return

        user = info["user"]

        username = (
            f"@{user['username']}"
            if user["username"]
            else "No username"
        )

        await query.edit_message_text(
            "👤 USER DETAILS\n\n"
            f"🆔 ID: {user['user_id']}\n"
            f"👤 Username: {username}\n"
            f"📝 Name: {user['first_name']}\n"
            f"💰 Points: {user['points']}\n"
            f"👥 Referrals: {info['referrals']}\n"
            f"📋 Tasks: {info['tasks']}",
            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "➕ Add Points",
                        callback_data=(
                            f"user_add_{target_id}"
                        )
                    ),

                    InlineKeyboardButton(
                        "➖ Remove Points",
                        callback_data=(
                            f"user_remove_{target_id}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Users",
                        callback_data="admin_users"
                    )
                ]
            ])
        )

        return


    # =========================
    # ADD USER POINTS
    # =========================

    if data.startswith(
        "user_add_"
    ):

        try:

            target_id = int(
                data.replace(
                    "user_add_",
                    "",
                    1
                )
            )

        except (
            ValueError,
            TypeError
        ):

            return

        context.user_data[
            "admin_action"
        ] = "user_add"

        context.user_data[
            "target_user"
        ] = target_id

        await query.message.reply_text(
            "➕ ADD POINTS\n\n"
            f"User ID: {target_id}\n\n"
            "কত Points add করবে?\n"
            "Example: 50"
        )

        return


    # =========================
    # REMOVE USER POINTS
    # =========================

    if data.startswith(
        "user_remove_"
    ):

        try:

            target_id = int(
                data.replace(
                    "user_remove_",
                    "",
                    1
                )
            )

        except (
            ValueError,
            TypeError
        ):

            return

        context.user_data[
            "admin_action"
        ] = "user_remove"

        context.user_data[
            "target_user"
        ] = target_id

        await query.message.reply_text(
            "➖ REMOVE POINTS\n\n"
            f"User ID: {target_id}\n\n"
            "কত Points remove করবে?\n"
            "Example: 50"
        )

        return


    # =========================
    # MANAGE TASKS
    # =========================

    if data == "admin_tasks":

        rows = get_channel_tasks()

        text = (
            "📋 MANAGE TASKS\n\n"
        )

        buttons = []

        if not rows:

            text += (
                "এখন কোনো active task নেই।"
            )

        else:

            for task in rows:

                text += (
                    f"#{task['id']} "
                    f"{task['title']}\n"
                    f"📢 {task['channel']}\n"
                    f"💰 +{task['reward']} Points\n\n"
                )

                buttons.append([
                    InlineKeyboardButton(
                        f"🗑 Delete #{task['id']}",
                        callback_data=(
                            f"delete_task_{task['id']}"
                        )
                    )
                ])

        buttons.append([
            InlineKeyboardButton(
                "➕ Add Task",
                callback_data="add_task"
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Admin Panel",
                callback_data="admin_back"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return


    # =========================
    # ADD TASK
    # =========================

    if data == "add_task":

        context.user_data[
            "admin_action"
        ] = "add_task"

        await query.message.reply_text(
            "➕ ADD NEW TASK\n\n"
            "এই format-এ পাঠাও:\n\n"
            "@channel | "
            "https://t.me/channel | "
            "Title | Reward\n\n"
            "Example:\n"
            "@mychannel | "
            "https://t.me/mychannel | "
            "📢 Join Channel | 10"
        )

        return


    # =========================
    # DELETE TASK
    # =========================

    if data.startswith(
        "delete_task_"
    ):

        try:

            task_id = int(
                data.replace(
                    "delete_task_",
                    "",
                    1
                )
            )

        except (
            ValueError,
            TypeError
        ):

            return

        delete_channel_task(
            task_id
        )

        await query.answer(
            "✅ Task deleted.",
            show_alert=True
        )

        await query.edit_message_text(
            "✅ Task deleted successfully.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📋 Manage Tasks",
                        callback_data="admin_tasks"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Admin Panel",
                        callback_data="admin_back"
                    )
                ]
            ])
        )

        return


    # =========================
    # WITHDRAWAL LIST
    # =========================

    if data == "admin_withdrawals":

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            """
        ).fetchall()

        conn.close()

        if not rows:

            await query.edit_message_text(
                "💳 কোনো pending withdrawal নেই।",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Admin Panel",
                            callback_data="admin_back"
                        )
                    ]
                ])
            )

            return

        text = (
            "💳 PENDING WITHDRAWALS\n\n"
        )

        buttons = []

        for row in rows:

            text += (
                f"🆔 #{row['id']}\n"
                f"👤 User: {row['user_id']}\n"
                f"💰 Amount: {row['amount']}\n"
                f"💳 Method: {row['method']}\n"
                f"📱 Account: {row['account']}\n\n"
            )

            buttons.append([

                InlineKeyboardButton(
                    f"✅ Approve #{row['id']}",
                    callback_data=(
                        f"approve_withdraw_{row['id']}"
                    )
                ),

                InlineKeyboardButton(
                    f"❌ Reject #{row['id']}",
                    callback_data=(
                        f"reject_withdraw_{row['id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Admin Panel",
                callback_data="admin_back"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return


    # =========================
    # APPROVE WITHDRAWAL
    # =========================

    if data.startswith(
        "approve_withdraw_"
    ):

        try:

            withdrawal_id = int(
                data.replace(
                    "approve_withdraw_",
                    "",
                    1
                )
            )

        except (
            ValueError,
            TypeError
        ):

            return

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE id=?
            """,
            (
                withdrawal_id,
            )
        ).fetchone()

        if not row:

            conn.close()

            await query.answer(
                "❌ Withdrawal not found.",
                show_alert=True
            )

            return

        if row["status"] != "pending":

            conn.close()

            await query.answer(
                "⚠️ Already processed.",
                show_alert=True
            )

            return

        conn.execute(
            """
            UPDATE withdrawals
            SET status='approved'
            WHERE id=?
            """,
            (
                withdrawal_id,
            )
        )

        conn.commit()
        conn.close()

        try:

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "✅ WITHDRAWAL APPROVED\n\n"
                    f"🆔 Request: #{withdrawal_id}\n"
                    f"💰 Amount: {row['amount']} Points\n"
                    f"💳 Method: {row['method']}\n\n"
                    "Payment process করা হবে।"
                )
            )

        except Exception:
            pass

        await query.answer(
            "✅ Withdrawal approved.",
            show_alert=True
        )

        await query.edit_message_text(
            "✅ Withdrawal approved successfully.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 Pending Withdrawals",
                        callback_data="admin_withdrawals"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Admin Panel",
                        callback_data="admin_back"
                    )
                ]
            ])
        )

        return
# =========================
# REJECT WITHDRAWAL
# =========================

async def reject_withdraw_callback(
    update,
    context
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ Unauthorized",
            show_alert=True
        )

        return

    await query.answer()

    try:

        withdrawal_id = int(
            query.data.replace(
                "reject_withdraw_",
                "",
                1
            )
        )

    except (
        ValueError,
        TypeError
    ):

        return

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id=?
        """,
        (
            withdrawal_id,
        )
    ).fetchone()

    if not row:

        conn.close()

        await query.answer(
            "❌ Withdrawal not found.",
            show_alert=True
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.answer(
            "⚠️ Already processed.",
            show_alert=True
        )

        return

    # Refund points when rejected
    conn.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id=?
        """,
        (
            row["amount"],
            row["user_id"]
        )
    )

    conn.execute(
        """
        UPDATE withdrawals
        SET status='rejected'
        WHERE id=?
        """,
        (
            withdrawal_id,
        )
    )

    conn.commit()
    conn.close()

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ WITHDRAWAL REJECTED\n\n"
                f"🆔 Request: #{withdrawal_id}\n"
                f"💰 Amount: {row['amount']} Points\n\n"
                f"💰 {row['amount']} Points "
                "তোমার balance-এ refund করা হয়েছে।"
            )
        )

    except Exception:
        pass

    await query.answer(
        "❌ Withdrawal rejected and refunded.",
        show_alert=True
    )

    await query.edit_message_text(
        "❌ Withdrawal rejected.\n\n"
        "💰 Points refunded to user.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💳 Pending Withdrawals",
                    callback_data="admin_withdrawals"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_back"
                )
            ]
        ])
    )


# =========================
# BROADCAST
# =========================

async def broadcast_start(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ You are not authorized."
        )

        return

    context.user_data[
        "admin_action"
    ] = "broadcast"

    await update.message.reply_text(
        "📢 BROADCAST\n\n"
        "যে message সবাইকে পাঠাতে চাও "
        "সেটা এখন পাঠাও।\n\n"
        "Text, emoji সব পাঠাতে পারো।\n"
        "Cancel করতে /cancel লিখো।"
    )


async def do_broadcast(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    message = update.message

    conn = db()

    rows = conn.execute(
        "SELECT user_id FROM users"
    ).fetchall()

    conn.close()

    total = len(rows)

    success = 0

    failed = 0

    await update.message.reply_text(
        "📢 Broadcast শুরু হয়েছে...\n\n"
        f"👥 Total Users: {total}"
    )

    for row in rows:

        try:

            await context.bot.copy_message(
                chat_id=row["user_id"],
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )

            success += 1

        except Exception as e:

            print(
                "Broadcast error:",
                e
            )

            failed += 1

    await update.message.reply_text(
        "✅ BROADCAST COMPLETE\n\n"
        f"👥 Total: {total}\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}",
        reply_markup=get_markup()
    )

    context.user_data.clear()


# =========================
# ADMIN TEXT ACTION
# =========================

async def admin_text_action(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return False

    action = context.user_data.get(
        "admin_action"
    )

    if not action:

        return False

    text = update.message.text.strip()


    # =========================
    # BROADCAST
    # =========================

    if action == "broadcast":

        await do_broadcast(
            update,
            context
        )

        return True


    # =========================
    # RENAME BUTTON
    # =========================

    if action.startswith(
        "rename_"
    ):

        key = action.replace(
            "rename_",
            "",
            1
        )

        if key in (
            "earn",
            "referral",
            "withdraw",
            "daily",
            "balance",
            "help"
        ):

            if len(text) > 50:

                await update.message.reply_text(
                    "❌ Button name সর্বোচ্চ 50 characters হতে পারে।"
                )

                return True

            if not text:

                await update.message.reply_text(
                    "❌ Empty name allowed নয়।"
                )

                return True

            set_setting(
                f"button_{key}",
                text
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ Button name updated!\n\n"
                f"New Name: {text}",
                reply_markup=get_markup()
            )

            return True


    # =========================
    # TASK REWARD
    # =========================

    if action == "reward_task":

        try:

            value = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if value < 0:

            await update.message.reply_text(
                "❌ Reward negative হতে পারবে না।"
            )

            return True

        set_setting(
            "reward_task",
            value
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Task reward updated!\n\n"
            f"📋 New Task Reward: {value}"
        )

        return True


    # =========================
    # REFERRAL REWARD
    # =========================

    if action == "reward_referral":

        try:

            value = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if value < 0:

            await update.message.reply_text(
                "❌ Reward negative হতে পারবে না।"
            )

            return True

        set_setting(
            "reward_referral",
            value
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Referral reward updated!\n\n"
            f"👥 New Referral Reward: {value}"
        )

        return True


    # =========================
    # DAILY REWARD
    # =========================

    if action == "reward_daily":

        try:

            value = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if value < 0:

            await update.message.reply_text(
                "❌ Reward negative হতে পারবে না।"
            )

            return True

        set_setting(
            "reward_daily",
            value
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Daily reward updated!\n\n"
            f"🎁 New Daily Reward: {value}"
        )

        return True


    # =========================
    # MIN WITHDRAW
    # =========================

    if action == "min_withdraw":

        try:

            value = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if value <= 0:

            await update.message.reply_text(
                "❌ Minimum withdraw 0-এর বেশি হতে হবে।"
            )

            return True

        set_setting(
            "min_withdraw",
            value
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Minimum withdrawal updated!\n\n"
            f"💳 New Minimum: {value} Points"
        )

        return True


    # =========================
    # ADD USER POINTS
    # =========================

    if action == "user_add":

        target_user = context.user_data.get(
            "target_user"
        )

        try:

            amount = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount অবশ্যই positive হতে হবে।"
            )

            return True

        if not target_user:

            context.user_data.clear()

            return True

        result = admin_add_points(
            target_user,
            amount
        )

        context.user_data.clear()

        if not result:

            await update.message.reply_text(
                "❌ User পাওয়া যায়নি।"
            )

            return True

        await update.message.reply_text(
            "✅ Points added successfully!\n\n"
            f"👤 User ID: {target_user}\n"
            f"💰 Added: +{amount}\n"
            f"📊 New Balance: {points(target_user)}"
        )

        try:

            await context.bot.send_message(
                chat_id=target_user,
                text=(
                    "🎉 POINTS ADDED\n\n"
                    f"💰 Admin added +{amount} Points.\n"
                    f"📊 New Balance: {points(target_user)}"
                )
            )

        except Exception:
            pass

        return True


    # =========================
    # REMOVE USER POINTS
    # =========================

    if action == "user_remove":

        target_user = context.user_data.get(
            "target_user"
        )

        try:

            amount = int(text)

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ শুধু number পাঠাও।"
            )

            return True

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount অবশ্যই positive হতে হবে।"
            )

            return True

        if not target_user:

            context.user_data.clear()

            return True

        result = admin_remove_points(
            target_user,
            amount
        )

        context.user_data.clear()

        if not result:

            await update.message.reply_text(
                "❌ User পাওয়া যায়নি।"
            )

            return True

        await update.message.reply_text(
            "✅ Points removed successfully!\n\n"
            f"👤 User ID: {target_user}\n"
            f"💰 Removed: -{amount}\n"
            f"📊 New Balance: {points(target_user)}"
        )

        try:

            await context.bot.send_message(
                chat_id=target_user,
                text=(
                    "ℹ️ POINTS UPDATED\n\n"
                    f"💰 Admin removed {amount} Points.\n"
                    f"📊 New Balance: {points(target_user)}"
                )
            )

        except Exception:
            pass

        return True


    # =========================
    # ADD TASK
    # =========================

    if action == "add_task":

        parts = [
            p.strip()
            for p in text.split("|")
        ]

        if len(parts) != 4:

            await update.message.reply_text(
                "❌ Format ভুল।\n\n"
                "@channel | "
                "https://t.me/channel | "
                "Title | Reward"
            )

            return True

        channel = parts[0]

        channel_url = parts[1]

        title = parts[2]

        try:

            reward = int(parts[3])

        except (
            ValueError,
            TypeError
        ):

            await update.message.reply_text(
                "❌ Reward অবশ্যই number হতে হবে।"
            )

            return True

        if not channel.startswith("@"):

            await update.message.reply_text(
                "❌ Channel username অবশ্যই @ দিয়ে শুরু হবে।"
            )

            return True

        if not channel_url.startswith(
            "https://t.me/"
        ):

            await update.message.reply_text(
                "❌ Channel URL অবশ্যই https://t.me/ দিয়ে শুরু হবে।"
            )

            return True

        if not title:

            await update.message.reply_text(
                "❌ Task title দিতে হবে।"
            )

            return True

        if reward <= 0:

            await update.message.reply_text(
                "❌ Reward 0-এর বেশি হতে হবে।"
            )

            return True

        add_channel_task(
            channel,
            channel_url,
            title,
            reward
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ NEW TASK ADDED\n\n"
            f"📢 Channel: {channel}\n"
            f"📝 Title: {title}\n"
            f"💰 Reward: +{reward}"
        )

        return True


    return False


# =========================
# CANCEL ADMIN ACTION
# =========================

async def cancel_command(
    update,
    context
):

    if is_admin(
        update.effective_user.id
    ):

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Action cancelled.",
            reply_markup=get_markup()
        )

        return True

    return False
# =========================
# GENERAL TEXT HANDLER
# =========================

async def all_text(
    update,
    context
):

    if not update.message:

        return

    user = update.effective_user

    if not user:

        return

    # Register user if needed
    register_user(user)

    # Admin action first
    if await admin_text_action(
        update,
        context
    ):

        return

    text = update.message.text

    # =========================
    # MAIN MENU
    # =========================

    if text == get_setting(
        "button_earn"
    ):

        if not feature_on("earn"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await earn_tasks_message(
            update,
            context
        )

        return


    if text == get_setting(
        "button_referral"
    ):

        if not feature_on("referral"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await referral_message(
            update,
            context
        )

        return


    if text == get_setting(
        "button_withdraw"
    ):

        if not feature_on("withdraw"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await withdraw_start(
            update,
            context
        )

        return


    if text == get_setting(
        "button_daily"
    ):

        if not feature_on("daily"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await daily_bonus_message(
            update,
            context
        )

        return


    if text == get_setting(
        "button_balance"
    ):

        if not feature_on("balance"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await balance_message(
            update,
            context
        )

        return


    if text == get_setting(
        "button_help"
    ):

        if not feature_on("help"):

            await update.message.reply_text(
                "❌ এই feature এখন বন্ধ আছে।"
            )

            return

        await help_message(
            update,
            context
        )

        return


    # =========================
    # UNKNOWN TEXT
    # =========================

    await update.message.reply_text(
        "❓ Command বুঝতে পারিনি।\n\n"
        "নিচের Menu থেকে একটি option select করো।",
        reply_markup=get_markup()
    )


# =========================
# CALLBACK HANDLER
# =========================

async def callback_handler(
    update,
    context
):

    query = update.callback_query

    data = query.data

    # =========================
    # ADMIN CALLBACKS
    # =========================

    if (
        data.startswith("admin_")
        or data.startswith("custom_")
        or data.startswith("rename_button_")
        or data.startswith("toggle_feature_")
        or data.startswith("set_reward_")
        or data.startswith("set_min_withdraw")
        or data.startswith("view_user_")
        or data.startswith("user_add_")
        or data.startswith("user_remove_")
        or data.startswith("delete_task_")
        or data == "add_task"
        or data.startswith("approve_withdraw_")
        or data.startswith("reject_withdraw_")
    ):

        # Withdrawal reject is handled separately
        if data.startswith(
            "reject_withdraw_"
        ):

            await reject_withdraw_callback(
                update,
                context
            )

            return

        await admin_callback(
            update,
            context
        )

        return


    # =========================
    # TASK VERIFY
    # =========================

    if data.startswith(
        "verify_task_"
    ):

        await verify_task_callback(
            update,
            context
        )

        return


    # =========================
    # UNKNOWN CALLBACK
    # =========================

    await query.answer(
        "❌ Unknown option.",
        show_alert=True
    )


# =========================
# ADMIN COMMAND
# =========================

async def admin_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ You are not authorized."
        )

        return

    await admin_panel(
        update,
        context
    )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update,
    context
):

    print(
        "ERROR:",
        context.error
    )


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    # Database
    init_db()

    # Default channel task
    create_default_task()

    # Render health server
    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    # Telegram application
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # =========================
    # WITHDRAW CONVERSATION
    # =========================

    withdraw_handler = ConversationHandler(

        entry_points=[
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                withdraw_start
            )
        ],

        states={

            AMOUNT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    withdraw_amount
                )
            ],

            METHOD: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    withdraw_method
                )
            ],

            ACCOUNT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    withdraw_account
                )
            ]
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                withdraw_cancel
            )
        ],

        allow_reentry=True
    )


    # =========================
    # COMMAND HANDLERS
    # =========================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )

    app.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )


    # =========================
    # WITHDRAW HANDLER
    # =========================

    app.add_handler(
        withdraw_handler
    )


    # =========================
    # CALLBACK HANDLER
    # =========================

    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )


    # =========================
    # TEXT HANDLER
    # =========================

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            all_text
        )
    )


    # =========================
    # ERROR HANDLER
    # =========================

    app.add_error_handler(
        error_handler
    )


    print(
        "🤖 TaskMint Bot is starting..."
    )

    print(
        "🌐 Health server running on port:",
        PORT
    )

    print(
        "🗄️ Database:",
        DB_FILE
    )

    print(
        "👑 Admin ID:",
        ADMIN_ID
    )

    # Start bot
    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
