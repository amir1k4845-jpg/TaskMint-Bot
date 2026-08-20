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
    return get_setting(
        f"feature_{feature}"
    ) == "1"


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
            "INSERT OR IGNORE INTO settings(key, value) "
            "VALUES(?, ?)",
            (key, value)
        )

    conn.commit()
    conn.close()


# =========================
# USER REGISTER
# =========================

def register_user(
    user,
    referrer=None
):

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
                setting_int(
                    "reward_referral",
                    REFERRAL_REWARD
                ),
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


def add_points(
    user_id,
    amount
):

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


def remove_points(
    user_id,
    amount
):

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


def admin_add_points(
    user_id,
    amount
):

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


def admin_remove_points(
    user_id,
    amount
):

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

def task_done(
    user_id,
    task_id
):

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


def save_task(
    user_id,
    task_id
):

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


def get_channel_task(
    task_id
):

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


def delete_channel_task(
    task_id
):

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
            setting_int(
                "reward_task",
                TASK_REWARD
            )
        ))

    conn.commit()
    conn.close()


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(
    BaseHTTPRequestHandler
):

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

async def start(
    update,
    context
):

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
``
# =========================
# START CONTINUED
# =========================

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

async def help_cmd(
    update,
    context
):

    if not feature_on("help"):
        await update.message.reply_text(
            "⚠️ Help feature এখন বন্ধ আছে.",
            reply_markup=get_markup()
        )
        return

    await update.message.reply_text(
        "ℹ️ TASKMINT HELP\n\n"
        "/start - Bot শুরু\n"
        "/help - Help\n"
        "/admin - Admin Panel\n\n"
        "💰 Earn Tasks করে Points earn করো।\n"
        "👥 Referral করে extra Points earn করো।\n"
        "🎁 প্রতিদিন Daily Bonus নাও।\n"
        "💳 Minimum Points হলে Withdraw করো।"
    )


# =========================
# EARN TASKS
# =========================

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
            "বর্তমানে কোনো Task available নেই।"
        )
        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"{row['title']} (+{row['reward']})",
                url=row["channel_url"]
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                f"✅ Check #{row['id']}",
                callback_data=f"check_task_{row['id']}"
            )
        ])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n"
        "1️⃣ প্রথমে Channel Join করো।\n"
        "2️⃣ তারপর Check চাপো।\n\n"
        "একটি Task একবারই reward দেবে।",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# TASK CHECK
# =========================

async def task_callback(
    update,
    context
):

    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if not data.startswith("check_task_"):
        return

    try:
        task_id = int(data.split("_")[2])
    except (ValueError, IndexError):
        return

    task = get_channel_task(task_id)

    if not task:
        await query.edit_message_text(
            "❌ এই Task আর available নেই।"
        )
        return

    task_key = f"channel_{task_id}"

    if task_done(user_id, task_key):

        await query.edit_message_text(
            "⚠️ এই Task তুমি আগেই complete করেছো!\n\n"
            f"💰 Points: {points(user_id)}"
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
                f"✅ +{task['reward']} Points\n"
                f"💰 Total Points: {points(user_id)}"
            )

        else:

            await query.edit_message_text(
                "❌ তুমি এখনো Channel Join করোনি!\n\n"
                "আগে Join করো, তারপর আবার Check করো।",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "📢 Join Channel",
                            url=task["channel_url"]
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Check Again",
                            callback_data=f"check_task_{task_id}"
                        )
                    ]
                ])
            )

    except Exception as e:

        print(
            "Task verification error:",
            e
        )

        await query.edit_message_text(
            "⚠️ Channel verification করা যাচ্ছে না।\n\n"
            "Bot-কে ওই Channel-এর Admin করা হয়েছে "
            "কিনা চেক করো।"
        )


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

    conn = db()

    row = conn.execute(
        """
        SELECT last_bonus
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    today = datetime.utcnow().strftime("%Y-%m-%d")

    if row and row["last_bonus"] == today:

        conn.close()

        await update.message.reply_text(
            "🎁 DAILY BONUS\n\n"
            "⚠️ আজকের Bonus তুমি already নিয়েছো।\n\n"
            "আগামীকাল আবার নিতে পারবে।"
        )

        return

    conn.execute(
        """
        UPDATE users
        SET points=points+?,
            last_bonus=?
        WHERE user_id=?
        """,
        (
            setting_int("reward_daily", DAILY_REWARD),
            today,
            user_id
        )
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎉 DAILY BONUS RECEIVED!\n\n"
        f"🎁 +{setting_int('reward_daily', DAILY_REWARD)} Points\n"
        f"💰 Total: {points(user_id)}"
    )


# =========================
# REFERRAL
# =========================

async def referral(
    update,
    context
):

    if not feature_on("referral"):
        await update.message.reply_text(
            "⚠️ Referral feature এখন বন্ধ আছে।",
            reply_markup=get_markup()
        )
        return

    user_id = update.effective_user.id

    conn = db()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE referred_by=?
        """,
        (user_id,)
    ).fetchone()[0]

    conn.close()

    bot_username = context.bot.username

    link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        "বন্ধুদের invite করে Points earn করো!\n\n"
        f"🎁 প্রতি Referral: +{setting_int('reward_referral', REFERRAL_REWARD)} Points\n"
        f"👤 Total Referrals: {total}\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{link}"
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
        return

    user_id = update.effective_user.id
    balance = points(user_id)

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    if balance < minimum:

        await update.message.reply_text(
            "💳 WITHDRAW\n\n"
            f"💰 Your Points: {balance}\n"
            f"⚠️ Minimum: {minimum} Points\n\n"
            "আরও Points earn করো।"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"💰 Available: {balance} Points\n"
        f"⚠️ Minimum: {minimum} Points\n\n"
        "কত Points withdraw করতে চাও?\n"
        "শুধু সংখ্যা পাঠাও।"
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
    balance = points(user_id)

    try:
        amount = int(
            update.message.text.strip()
        )
    except ValueError:

        await update.message.reply_text(
            "❌ শুধু সংখ্যা পাঠাও।\n\n"
            "Example: 100"
        )

        return AMOUNT

    minimum = setting_int(
        "min_withdraw",
        MIN_WITHDRAW
    )

    if amount < minimum:

        await update.message.reply_text(
            f"❌ Minimum {minimum} Points।"
        )

        return AMOUNT

    if amount > balance:

        await update.message.reply_text(
            "❌ তোমার কাছে এত Points নেই!\n\n"
            f"💰 Available: {balance}"
        )

        return AMOUNT

    context.user_data["withdraw_amount"] = amount

    await update.message.reply_text(
        "💳 Payment Method নির্বাচন করো:",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["💰 Binance"],
                ["📱 bKash"],
                ["📱 Nagad"]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
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

    if method not in (
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad"
    ):

        await update.message.reply_text(
            "❌ একটি valid Payment Method নির্বাচন করো।"
        )

        return METHOD

    context.user_data["withdraw_method"] = method

    await update.message.reply_text(
        "📱 এখন তোমার Payment Account পাঠাও।\n\n"
        "উদাহরণ:\n"
        "Binance UID / Email\n"
        "bKash Number\n"
        "Nagad Number"
    )

    return ACCOUNT
    if data == "admin_add_task":

        context.user_data[
            "admin_action"
        ] = "add_task_channel"

        await query.message.reply_text(
            "➕ ADD NEW TASK\n\n"
            "প্রথমে Channel Username পাঠাও।\n\n"
            "Example:\n"
            "@Amir10m300"
        )

        return

    if data == "admin_delete_task":

        rows = get_channel_tasks()

        if not rows:

            await query.edit_message_text(
                "🗑 DELETE TASK\n\n"
                "কোনো active task নেই।"
            )

            return

        keyboard = []

        for row in rows:

            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 {row['title']} (+{row['reward']})",
                    callback_data=f"delete_task_{row['id']}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "🔙 Admin Panel",
                callback_data="admin_home"
            )
        ])

        await query.edit_message_text(
            "🗑 DELETE TASK\n\n"
            "যে Task delete করতে চাও সেটিতে চাপো:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    if data.startswith("delete_task_"):

        try:
            task_id = int(
                data.replace(
                    "delete_task_",
                    ""
                )
            )

        except ValueError:

            await query.answer(
                "❌ Invalid Task",
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

        delete_channel_task(
            task_id
        )

        await query.edit_message_text(
            "✅ TASK DELETED!\n\n"
            f"🗑 {task['title']}\n"
            f"💰 Reward: {task['reward']} Points"
        )

        return


# =========================
# ADMIN TEXT ACTIONS
# =========================

async def process_admin_text(
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
    # ADD POINTS
    # =========================

    if action in (
        "add_points",
        "remove_points"
    ):

        parts = text.split()

        if len(parts) != 2:

            await update.message.reply_text(
                "❌ Format ভুল।\n\n"
                "Example:\n"
                "123456789 50"
            )

            return True

        try:

            target_user = int(
                parts[0]
            )

            amount = int(
                parts[1]
            )

        except ValueError:

            await update.message.reply_text(
                "❌ USER_ID এবং amount অবশ্যই সংখ্যা হতে হবে।"
            )

            return True

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount 0-এর বেশি হতে হবে।"
            )

            return True

        if action == "add_points":

            success = admin_add_points(
                target_user,
                amount
            )

            action_text = "added"

        else:

            success = admin_remove_points(
                target_user,
                amount
            )

            action_text = "removed"

        if not success:

            await update.message.reply_text(
                "❌ User পাওয়া যায়নি।"
            )

            return True

        context.user_data.pop(
            "admin_action",
            None
        )

        await update.message.reply_text(
            f"✅ {amount} Points {action_text} successfully!\n\n"
            f"👤 User ID: {target_user}\n"
            f"💰 Current Balance: {points(target_user)}"
        )

        return True

    # =========================
    # ADD TASK - CHANNEL
    # =========================

    if action == "add_task_channel":

        channel = text

        if not channel.startswith("@"):

            await update.message.reply_text(
                "❌ Channel username অবশ্যই @ দিয়ে শুরু হবে।\n\n"
                "Example:\n"
                "@Amir10m300"
            )

            return True

        context.user_data[
            "new_task_channel"
        ] = channel

        context.user_data[
            "admin_action"
        ] = "add_task_link"

        await update.message.reply_text(
            "🔗 এখন Channel-এর invite/link পাঠাও।\n\n"
            "Example:\n"
            "https://t.me/Amir10m300"
        )

        return True

    # =========================
    # ADD TASK - LINK
    # =========================

    if action == "add_task_link":

        link = text

        if not (
            link.startswith("https://t.me/")
            or link.startswith("http://t.me/")
        ):

            await update.message.reply_text(
                "❌ Valid Telegram link পাঠাও।\n\n"
                "Example:\n"
                "https://t.me/Amir10m300"
            )

            return True

        context.user_data[
            "new_task_link"
        ] = link

        context.user_data[
            "admin_action"
        ] = "add_task_title"

        await update.message.reply_text(
            "📝 এখন Task-এর Title পাঠাও।\n\n"
            "Example:\n"
            "📢 Join Our Channel"
        )

        return True

    # =========================
    # ADD TASK - TITLE
    # =========================

    if action == "add_task_title":

        if not text:

            await update.message.reply_text(
                "❌ Title empty হতে পারবে না।"
            )

            return True

        context.user_data[
            "new_task_title"
        ] = text

        context.user_data[
            "admin_action"
        ] = "add_task_reward"

        await update.message.reply_text(
            "💰 এখন এই Task-এর Reward পাঠাও।\n\n"
            "Example:\n"
            "10"
        )

        return True

    # =========================
    # ADD TASK - REWARD
    # =========================

    if action == "add_task_reward":

        try:

            reward = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Reward অবশ্যই সংখ্যা হতে হবে।\n\n"
                "Example: 10"
            )

            return True

        if reward <= 0:

            await update.message.reply_text(
                "❌ Reward 0-এর বেশি হতে হবে।"
            )

            return True

        channel = context.user_data.get(
            "new_task_channel"
        )

        link = context.user_data.get(
            "new_task_link"
        )

        title = context.user_data.get(
            "new_task_title"
        )

        if not all([
            channel,
            link,
            title
        ]):

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Task data missing হয়েছে। "
                "আবার Add Task করো।"
            )

            return True

        add_channel_task(
            channel,
            link,
            title,
            reward
        )

        context.user_data.clear()

        await update.message.reply_text(
            "🎉 TASK ADDED SUCCESSFULLY!\n\n"
            f"📢 Channel: {channel}\n"
            f"📝 Title: {title}\n"
            f"💰 Reward: {reward} Points\n"
            f"🔗 Link: {link}"
        )

        return True

    return False


# =========================
# ADMIN WITHDRAW MANAGEMENT
# =========================

def pending_withdrawals():

    conn = db()

    rows = conn.execute("""
    SELECT *
    FROM withdrawals
    WHERE status='pending'
    ORDER BY id ASC
    """).fetchall()

    conn.close()

    return rows


async def show_pending_withdrawals(
    update,
    context
):

    query = update.callback_query

    rows = pending_withdrawals()

    if not rows:

        await query.edit_message_text(
            "💳 PENDING WITHDRAWALS\n\n"
            "✅ কোনো pending withdrawal নেই।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin_home"
                    )
                ]
            ])
        )

        return

    keyboard = []

    for row in rows:

        keyboard.append([
            InlineKeyboardButton(
                f"#{row['id']} • "
                f"{row['amount']} Points • "
                f"{row['method']}",
                callback_data=f"withdraw_view_{row['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_home"
        )
    ])

    await query.edit_message_text(
        "💳 PENDING WITHDRAWALS\n\n"
        "একটি request নির্বাচন করো:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def view_withdrawal(
    update,
    context,
    withdrawal_id
):

    query = update.callback_query

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id=?
        """,
        (withdrawal_id,)
    ).fetchone()

    conn.close()

    if not row:

        await query.edit_message_text(
            "❌ Withdrawal request পাওয়া যায়নি।"
        )

        return

    status = row["status"]

    keyboard = []

    if status == "pending":

        keyboard.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"withdraw_approve_{row['id']}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"withdraw_reject_{row['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Pending Withdrawals",
            callback_data="admin_withdrawals"
        )
    ])

    await query.edit_message_text(
        "💳 WITHDRAWAL REQUEST\n\n"
        f"🆔 Request ID: #{row['id']}\n"
        f"👤 User ID: {row['user_id']}\n"
        f"👤 Username: @{row['username']}\n"
        f"💰 Amount: {row['amount']} Points\n"
        f"💳 Method: {row['method']}\n"
        f"📱 Account: {row['account']}\n"
        f"📌 Status: {status}",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def approve_withdrawal(
    update,
    context,
    withdrawal_id
):

    query = update.callback_query

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id=?
        """,
        (withdrawal_id,)
    ).fetchone()

    if not row:

        conn.close()

        await query.answer(
            "❌ Request পাওয়া যায়নি।",
            show_alert=True
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.answer(
            "⚠️ এই request already processed।",
            show_alert=True
        )

        return

    conn.execute(
        """
        UPDATE withdrawals
        SET status='approved'
        WHERE id=?
        """,
        (withdrawal_id,)
    )

    conn.commit()
    conn.close()

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ WITHDRAW APPROVED!\n\n"
                f"🆔 Request ID: #{row['id']}\n"
                f"💰 Amount: {row['amount']} Points\n"
                f"💳 Method: {row['method']}\n"
                f"📱 Account: {row['account']}\n\n"
                "Admin তোমার withdrawal approve করেছে।"
            )
        )

    except Exception as e:

        print(
            "Withdraw approval notification error:",
            e
        )

    await query.edit_message_text(
        "✅ WITHDRAW APPROVED!\n\n"
        f"🆔 Request: #{row['id']}\n"
        f"👤 User: {row['user_id']}\n"
        f"💰 Amount: {row['amount']} Points\n"
        f"📌 Status: approved",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Pending Withdrawals",
                    callback_data="admin_withdrawals"
                )
            ]
        ])
    )


async def reject_withdrawal(
    update,
    context,
    withdrawal_id
):

    query = update.callback_query

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id=?
        """,
        (withdrawal_id,)
    ).fetchone()

    if not row:

        conn.close()

        await query.answer(
            "❌ Request পাওয়া যায়নি।",
            show_alert=True
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.answer(
            "⚠️ এই request already processed।",
            show_alert=True
        )

        return

    conn.execute(
        """
        UPDATE withdrawals
        SET status='rejected'
        WHERE id=?
        """,
        (withdrawal_id,)
    )

    # Return the points to the user.
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

    conn.commit()
    conn.close()

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ WITHDRAW REJECTED!\n\n"
                f"🆔 Request ID: #{row['id']}\n"
                f"💰 Returned: {row['amount']} Points\n\n"
                "তোমার Points আবার Balance-এ যোগ করা হয়েছে।"
            )
        )

    except Exception as e:

        print(
            "Withdraw rejection notification error:",
            e
        )

    await query.edit_message_text(
        "❌ WITHDRAW REJECTED!\n\n"
        f"🆔 Request: #{row['id']}\n"
        f"👤 User: {row['user_id']}\n"
        f"💰 Returned: {row['amount']} Points\n"
        f"📌 Status: rejected",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Pending Withdrawals",
                    callback_data="admin_withdrawals"
                )
            ]
        ])
        )
