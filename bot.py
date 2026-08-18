import os
import sqlite3
import threading
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
                REFERRAL_REWARD,
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
            TASK_REWARD
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
        reply_markup=MARKUP
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
                callback_data=(
                    f"check_task_{row['id']}"
                )
            )
        ])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n"
        "1️⃣ প্রথমে Channel Join করো।\n"
        "2️⃣ তারপর Check চাপো।\n\n"
        "একটি Task একবারই reward দেবে।",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
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
                f"💰 Total Points: "
                f"{points(user_id)}"
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
                            callback_data=(
                                f"check_task_{task_id}"
                            )
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

    today = datetime.utcnow().strftime(
        "%Y-%m-%d"
    )

    if (
        row
        and row["last_bonus"] == today
    ):

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
            DAILY_REWARD,
            today,
            user_id
        )
    )

    conn.commit()

    conn.close()

    await update.message.reply_text(
        "🎉 DAILY BONUS RECEIVED!\n\n"
        f"🎁 +{DAILY_REWARD} Points\n"
        f"💰 Total: {points(user_id)}"
    )


# =========================
# REFERRAL
# =========================

async def referral(
    update,
    context
):

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

    bot_username = (
        context.bot.username
    )

    link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        "বন্ধুদের invite করে Points earn করো!\n\n"
        f"🎁 প্রতি Referral: "
        f"+{REFERRAL_REWARD} Points\n"
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

    user_id = update.effective_user.id

    balance = points(user_id)

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            "💳 WITHDRAW\n\n"
            f"💰 Your Points: {balance}\n"
            f"⚠️ Minimum: "
            f"{MIN_WITHDRAW} Points\n\n"
            "আরও Points earn করো।"
        )

        return (
            ConversationHandler.END
        )

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"💰 Available: {balance} Points\n"
        f"⚠️ Minimum: "
        f"{MIN_WITHDRAW} Points\n\n"
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

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            f"❌ Minimum "
            f"{MIN_WITHDRAW} Points।"
        )

        return AMOUNT

    if amount > balance:

        await update.message.reply_text(
            "❌ তোমার কাছে এত Points নেই!\n\n"
            f"💰 Available: {balance}"
        )

        return AMOUNT

    context.user_data[
        "withdraw_amount"
    ] = amount

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

    method = (
        update.message.text.strip()
    )

    if method not in (
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad"
    ):

        await update.message.reply_text(
            "❌ একটি valid payment method "
            "নির্বাচন করো।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
        "📱 এখন তোমার payment account "
        "number / ID পাঠাও।"
    )

    return ACCOUNT


# =========================
# WITHDRAW ACCOUNT
# =========================

async def withdraw_account(
    update,
    context
):

    account = (
        update.message.text.strip()
    )

    if len(account) < 4:

        await update.message.reply_text(
            "❌ সঠিক account number / ID দাও।"
        )

        return ACCOUNT

    user_id = update.effective_user.id

    username = (
        update.effective_user.username
        or "No Username"
    )

    amount = context.user_data[
        "withdraw_amount"
    ]

    method = context.user_data[
        "withdraw_method"
    ]

    if amount > points(user_id):

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Balance পরিবর্তিত হয়েছে। "
            "আবার চেষ্টা করো।",
            reply_markup=MARKUP
        )

        return (
            ConversationHandler.END
        )

    remove_points(
        user_id,
        amount
    )

    conn = db()

    cur = conn.execute(
        """
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
        """,
        (
            user_id,
            username,
            amount,
            method,
            account
        )
    )

    withdrawal_id = cur.lastrowid

    conn.commit()

    conn.close()

    admin_text = (
        "🔔 NEW WITHDRAWAL\n\n"
        f"🆔 Request: #{withdrawal_id}\n"
        f"👤 User ID: {user_id}\n"
        f"👤 Username: @{username}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n"
        f"💰 Remaining: {points(user_id)}"
    )

    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=(
                    f"approve_{withdrawal_id}"
                )
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=(
                    f"reject_{withdrawal_id}"
                )
            )
        ]
    ])

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            reply_markup=admin_keyboard
        )

    except Exception as e:

        print(
            "Admin notification error:",
            e
        )

    await update.message.reply_text(
        "✅ Withdrawal Request Submitted!\n\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n\n"
        "⏳ Admin verification-এর জন্য "
        "অপেক্ষা করো।",
        reply_markup=MARKUP
    )

    context.user_data.clear()

    return (
        ConversationHandler.END
    )
# =========================
# ADMIN PANEL
# =========================

async def admin_panel(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Unauthorized."
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📋 Manage Tasks",
                callback_data="manage_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Total Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 User Management",
                callback_data="user_management"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Users List",
                callback_data="users_list"
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
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ]
    ])

    await update.message.reply_text(
        "👑 ADMIN PANEL\n\n"
        "TaskMint Control Center",
        reply_markup=keyboard
    )


# =========================
# TASK MANAGER
# =========================

async def task_manager_menu(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Channel Task",
                callback_data="task_add"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Active Tasks",
                callback_data="task_list"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ Delete Task",
                callback_data="task_delete"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Admin Panel",
                callback_data="admin_home"
            )
        ]
    ])

    await query.edit_message_text(
        "📋 TASK MANAGER\n\n"
        "এখান থেকে Channel Task "
        "manage করতে পারবে।",
        reply_markup=keyboard
    )


# =========================
# ADD TASK START
# =========================

async def add_task_start(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    context.user_data[
        "task_add_step"
    ] = "channel"

    await query.edit_message_text(
        "➕ ADD CHANNEL TASK\n\n"
        "প্রথমে Channel Username পাঠাও।\n\n"
        "Example:\n"
        "@MyChannel"
    )


# =========================
# TASK LIST
# =========================

async def task_list(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    rows = get_channel_tasks()

    if not rows:

        text = (
            "📋 ACTIVE TASKS\n\n"
            "কোনো Task নেই।"
        )

    else:

        text = (
            "📋 ACTIVE TASKS\n\n"
        )

        for row in rows:

            text += (
                f"🆔 ID: {row['id']}\n"
                f"📢 {row['channel']}\n"
                f"📝 {row['title']}\n"
                f"💰 Reward: "
                f"{row['reward']} Points\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Task Manager",
                    callback_data="manage_tasks"
                )
            ]
        ])
    )


# =========================
# DELETE TASK MENU
# =========================

async def task_delete_menu(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    rows = get_channel_tasks()

    if not rows:

        await query.edit_message_text(
            "🗑️ DELETE TASK\n\n"
            "কোনো active task নেই।"
        )

        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"🗑️ #{row['id']} "
                f"{row['title']}",
                callback_data=(
                    f"delete_task_{row['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Task Manager",
            callback_data="manage_tasks"
        )
    ])

    await query.edit_message_text(
        "🗑️ DELETE TASK\n\n"
        "যে Task delete করতে চাও "
        "সেটি নির্বাচন করো:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================
# USER MANAGEMENT
# =========================

async def user_management(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    context.user_data[
        "user_management"
    ] = True

    await query.edit_message_text(
        "👤 USER MANAGEMENT\n\n"
        "যে User-এর information দেখতে চাও,\n"
        "তার Telegram User ID পাঠাও।\n\n"
        "Example:\n"
        "123456789"
    )


# =========================
# SHOW USER INFORMATION
# =========================

async def show_user_information(
    update,
    context,
    user_id
):

    info = get_user_info(
        user_id
    )

    if not info:

        await update.message.reply_text(
            "❌ এই User ID-তে "
            "কোনো user পাওয়া যায়নি।"
        )

        return

    user = info["user"]

    username = user["username"]

    if username:

        username_text = (
            f"@{username}"
        )

    else:

        username_text = (
            "No Username"
        )

    text = (
        "👤 USER INFORMATION\n\n"
        f"🆔 User ID: "
        f"{user['user_id']}\n"
        f"👤 Username: "
        f"{username_text}\n"
        f"📝 Name: "
        f"{user['first_name'] or 'Unknown'}\n"
        f"💰 Points: "
        f"{user['points']}\n"
        f"👥 Referrals: "
        f"{info['referrals']}\n"
        f"✅ Completed Tasks: "
        f"{info['tasks']}\n"
        f"🎁 Last Bonus: "
        f"{user['last_bonus'] or 'Not taken'}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Points",
                callback_data=(
                    f"user_add_{user_id}"
                )
            ),
            InlineKeyboardButton(
                "➖ Remove Points",
                callback_data=(
                    f"user_remove_{user_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Admin Panel",
                callback_data="admin_home"
            )
        ]
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard
    )


# =========================
# USERS LIST
# =========================

async def users_list(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    conn = db()

    rows = conn.execute("""
    SELECT user_id, username,
           first_name, points
    FROM users
    ORDER BY user_id DESC
    LIMIT 30
    """).fetchall()

    conn.close()

    if not rows:

        await query.edit_message_text(
            "📋 USERS LIST\n\n"
            "কোনো User নেই।"
        )

        return

    text = (
        "📋 USERS LIST\n\n"
    )

    for row in rows:

        username = row["username"]

        if username:

            username = (
                "@" + username
            )

        else:

            username = "No Username"

        text += (
            f"🆔 {row['user_id']}\n"
            f"👤 {username}\n"
            f"📝 {row['first_name']}\n"
            f"💰 {row['points']} Points\n"
            "──────────────\n"
        )

    text += (
        "\n⚠️ সর্বশেষ 30 জন User দেখানো হয়েছে।"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin_home"
                )
            ]
        ])
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized!",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data


    # USER MANAGEMENT

    if data == "user_management":

        await user_management(
            update,
            context
        )

        return


    # USERS LIST

    if data == "users_list":

        await users_list(
            update,
            context
        )

        return


    # ADD POINTS

    if data.startswith(
        "user_add_"
    ):

        user_id = int(
            data.replace(
                "user_add_",
                ""
            )
        )

        context.user_data[
            "points_action"
        ] = "add"

        context.user_data[
            "points_user_id"
        ] = user_id

        await query.message.reply_text(
            "➕ ADD POINTS\n\n"
            "কত Points add করতে চাও?\n\n"
            "Example: 100"
        )

        return


    # REMOVE POINTS

    if data.startswith(
        "user_remove_"
    ):

        user_id = int(
            data.replace(
                "user_remove_",
                ""
            )
        )

        context.user_data[
            "points_action"
        ] = "remove"

        context.user_data[
            "points_user_id"
        ] = user_id

        await query.message.reply_text(
            "➖ REMOVE POINTS\n\n"
            "কত Points remove করতে চাও?\n\n"
            "Example: 100"
        )

        return


    # TASK MANAGER

    if data == "manage_tasks":

        await task_manager_menu(
            update,
            context
        )

        return


    # ADD TASK

    if data == "task_add":

        await add_task_start(
            update,
            context
        )

        return


    # TASK LIST

    if data == "task_list":

        await task_list(
            update,
            context
        )

        return


    # DELETE TASK MENU

    if data == "task_delete":

        await task_delete_menu(
            update,
            context
        )

        return


    # DELETE TASK

    if data.startswith(
        "delete_task_"
    ):

        task_id = int(
            data.replace(
                "delete_task_",
                ""
            )
        )

        delete_channel_task(
            task_id
        )

        await query.edit_message_text(
            "✅ Task Deleted Successfully!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Task Manager",
                        callback_data="manage_tasks"
                    )
                ]
            ])
        )

        return


    # ADMIN HOME

    if data == "admin_home":

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📋 Manage Tasks",
                    callback_data="manage_tasks"
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Total Users",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 User Management",
                    callback_data="user_management"
                )
            ],
            [
                InlineKeyboardButton(
                    "📋 Users List",
                    callback_data="users_list"
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
                    "📊 Statistics",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    "📢 Broadcast",
                    callback_data="admin_broadcast"
                )
            ]
        ])

        await query.edit_message_text(
            "👑 ADMIN PANEL\n\n"
            "TaskMint Control Center",
            reply_markup=keyboard
        )

        return
# =========================
# ADMIN USERS
# =========================

async def admin_users(update, context):

    query = update.callback_query

    await query.answer()

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    conn.close()

    await query.edit_message_text(
        "👥 TOTAL USERS\n\n"
        f"👤 Total Registered Users: {total}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📋 Users List",
                    callback_data="users_list"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin_home"
                )
            ]
        ])
    )


# =========================
# ADMIN STATISTICS
# =========================

async def admin_statistics(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    total_points = conn.execute(
        """
        SELECT COALESCE(
            SUM(points), 0
        )
        FROM users
        """
    ).fetchone()[0]

    completed_tasks = conn.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        """
    ).fetchone()[0]

    pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status='pending'
        """
    ).fetchone()[0]

    approved = conn.execute(
        """
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status='approved'
        """
    ).fetchone()[0]

    rejected = conn.execute(
        """
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status='rejected'
        """
    ).fetchone()[0]

    conn.close()

    text = (
        "📊 BOT STATISTICS\n\n"
        f"👥 Total Users: {users}\n"
        f"💰 Total User Points: {total_points}\n"
        f"✅ Completed Tasks: "
        f"{completed_tasks}\n"
        f"⏳ Pending Withdrawals: "
        f"{pending}\n"
        f"✅ Approved Withdrawals: "
        f"{approved}\n"
        f"❌ Rejected Withdrawals: "
        f"{rejected}"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin_home"
                )
            ]
        ])
    )


# =========================
# PENDING WITHDRAWALS
# =========================

async def admin_withdrawals(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE status='pending'
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    conn.close()

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

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"💳 #{row['id']} - "
                f"{row['amount']} Points",
                callback_data=(
                    f"withdraw_view_{row['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_home"
        )
    ])

    await query.edit_message_text(
        "💳 PENDING WITHDRAWALS\n\n"
        "Request select করো:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================
# WITHDRAW VIEW
# =========================

async def withdrawal_view(
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

    text = (
        "💳 WITHDRAWAL DETAILS\n\n"
        f"🆔 Request ID: #{row['id']}\n"
        f"👤 User ID: {row['user_id']}\n"
        f"👤 Username: @{row['username']}\n"
        f"💰 Amount: {row['amount']} Points\n"
        f"💳 Method: {row['method']}\n"
        f"📱 Account: {row['account']}\n"
        f"📌 Status: {row['status']}"
    )

    if row["status"] == "pending":

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=(
                        f"approve_{row['id']}"
                    )
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=(
                        f"reject_{row['id']}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Pending",
                    callback_data="admin_withdrawals"
                )
            ]
        ])

    else:

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Pending",
                    callback_data="admin_withdrawals"
                )
            ]
        ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


# =========================
# BROADCAST START
# =========================

async def broadcast_start(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    context.user_data[
        "broadcast"
    ] = True

    await query.edit_message_text(
        "📢 BROADCAST\n\n"
        "এখন যে Message সবাইকে পাঠাতে চাও "
        "সেটা পাঠাও।\n\n"
        "Text, Photo অথবা অন্য message "
        "পাঠাতে পারো।"
    )


# =========================
# ADMIN CALLBACK CONTINUED
# =========================

async def admin_callback_continue(
    update,
    context
):

    query = update.callback_query

    data = query.data

    # TOTAL USERS

    if data == "admin_users":

        await admin_users(
            update,
            context
        )

        return True


    # STATISTICS

    if data == "admin_stats":

        await admin_statistics(
            update,
            context
        )

        return True


    # WITHDRAWALS

    if data == "admin_withdrawals":

        await admin_withdrawals(
            update,
            context
        )

        return True


    # WITHDRAW VIEW

    if data.startswith(
        "withdraw_view_"
    ):

        withdrawal_id = int(
            data.replace(
                "withdraw_view_",
                ""
            )
        )

        await query.answer()

        await withdrawal_view(
            update,
            context,
            withdrawal_id
        )

        return True


    # BROADCAST

    if data == "admin_broadcast":

        await broadcast_start(
            update,
            context
        )

        return True


    return False


# =========================
# APPROVE WITHDRAWAL
# =========================

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
            "Request not found!",
            show_alert=True
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.answer(
            "Already processed!",
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

    await query.edit_message_text(
        "✅ WITHDRAWAL APPROVED\n\n"
        f"🆔 Request: #{withdrawal_id}\n"
        f"👤 User ID: {row['user_id']}\n"
        f"💰 Amount: {row['amount']} Points\n"
        f"💳 Method: {row['method']}\n"
        f"📱 Account: {row['account']}"
    )

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ Withdrawal Approved!\n\n"
                f"💰 Amount: "
                f"{row['amount']} Points\n"
                f"💳 Method: "
                f"{row['method']}\n\n"
                "Payment processing করা হবে।"
            )
        )

    except Exception as e:

        print(
            "Approve notification error:",
            e
        )


# =========================
# REJECT WITHDRAWAL
# =========================

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
            "Request not found!",
            show_alert=True
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.answer(
            "Already processed!",
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

    # Return points to user

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

    await query.edit_message_text(
        "❌ WITHDRAWAL REJECTED\n\n"
        f"🆔 Request: #{withdrawal_id}\n"
        f"👤 User ID: {row['user_id']}\n"
        f"💰 Amount: {row['amount']} Points\n\n"
        "↩️ Points user balance-এ ফেরত দেওয়া হয়েছে।"
    )

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ Withdrawal Rejected\n\n"
                f"💰 {row['amount']} Points "
                "তোমার balance-এ ফেরত দেওয়া হয়েছে।"
            )
        )

    except Exception as e:

        print(
            "Reject notification error:",
            e
        )


# =========================
# ADMIN TEXT HANDLER
# =========================

async def admin_text_handler(
    update,
    context
):

    if update.effective_user.id != ADMIN_ID:

        return

    text = (
        update.message.text.strip()
    )


    # =====================
    # BROADCAST
    # =====================

    if context.user_data.get(
        "broadcast"
    ):

        context.user_data[
            "broadcast"
        ] = False

        conn = db()

        users = conn.execute(
            "SELECT user_id FROM users"
        ).fetchall()

        conn.close()

        sent = 0
        failed = 0

        for user in users:

            try:

                await context.bot.copy_message(
                    chat_id=user["user_id"],
                    from_chat_id=(
                        update.effective_chat.id
                    ),
                    message_id=(
                        update.message.message_id
                    )
                )

                sent += 1

            except Exception:

                failed += 1

        await update.message.reply_text(
            "📢 BROADCAST FINISHED!\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )

        return


    # =====================
    # USER MANAGEMENT
    # =====================

    if context.user_data.get(
        "user_management"
    ):

        try:

            user_id = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ User ID শুধু সংখ্যা হতে হবে।"
            )

            return

        context.user_data[
            "user_management"
        ] = False

        await show_user_information(
            update,
            context,
            user_id
        )

        return


    # =====================
    # POINTS ACTION
    # =====================

    if context.user_data.get(
        "points_action"
    ):

        try:

            amount = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ শুধু সংখ্যা পাঠাও।"
            )

            return

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount 0-এর বেশি হতে হবে।"
            )

            return

        user_id = context.user_data[
            "points_user_id"
        ]

        action = context.user_data[
            "points_action"
        ]

        if action == "add":

            success = admin_add_points(
                user_id,
                amount
            )

            action_text = "added"

        else:

            success = admin_remove_points(
                user_id,
                amount
            )

            action_text = "removed"

        context.user_data.pop(
            "points_action",
            None
        )

        context.user_data.pop(
            "points_user_id",
            None
        )

        if not success:

            await update.message.reply_text(
                "❌ User পাওয়া যায়নি।"
            )

            return

        await update.message.reply_text(
            "✅ POINTS UPDATED\n\n"
            f"👤 User ID: {user_id}\n"
            f"💰 {amount} Points {action_text}\n"
            f"💳 Current Balance: "
            f"{points(user_id)}"
        )

        return
# =========================
# ADMIN TEXT - TASK ADD
# =========================

    step = context.user_data.get(
        "task_add_step"
    )

    if step == "channel":

        context.user_data[
            "new_channel"
        ] = text

        context.user_data[
            "task_add_step"
        ] = "url"

        await update.message.reply_text(
            "🔗 এখন Channel-এর link পাঠাও।\n\n"
            "Example:\n"
            "https://t.me/MyChannel"
        )

        return


    if step == "url":

        context.user_data[
            "new_channel_url"
        ] = text

        context.user_data[
            "task_add_step"
        ] = "title"

        await update.message.reply_text(
            "📝 এখন Task-এর নাম পাঠাও।\n\n"
            "Example:\n"
            "📢 Join Channel"
        )

        return


    if step == "title":

        context.user_data[
            "new_task_title"
        ] = text

        context.user_data[
            "task_add_step"
        ] = "reward"

        await update.message.reply_text(
            "💰 এখন Reward Points পাঠাও।\n\n"
            "Example:\n"
            "10"
        )

        return


    if step == "reward":

        try:

            reward = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Reward শুধু সংখ্যা হতে হবে।"
            )

            return

        if reward <= 0:

            await update.message.reply_text(
                "❌ Reward 0-এর বেশি হতে হবে।"
            )

            return

        channel = context.user_data[
            "new_channel"
        ]

        channel_url = context.user_data[
            "new_channel_url"
        ]

        title = context.user_data[
            "new_task_title"
        ]

        add_channel_task(
            channel,
            channel_url,
            title,
            reward
        )

        context.user_data.clear()

        await update.message.reply_text(
            "🎉 TASK ADDED SUCCESSFULLY!\n\n"
            f"📢 Channel: {channel}\n"
            f"📝 Title: {title}\n"
            f"💰 Reward: {reward} Points",
            reply_markup=MARKUP
        )

        return


# =========================
# NORMAL BUTTON HANDLER
# =========================

async def button_handler(
    update,
    context
):

    if not update.message:

        return

    text = update.message.text


    if text == "💰 Earn Tasks":

        await earn_tasks(
            update,
            context
        )


    elif text == "👥 Refer & Earn":

        await referral(
            update,
            context
        )


    elif text == "🎁 Daily Bonus":

        await daily_bonus(
            update,
            context
        )


    elif text == "📊 My Balance":

        user_id = (
            update.effective_user.id
        )

        await update.message.reply_text(
            "📊 MY BALANCE\n\n"
            f"💰 Points: "
            f"{points(user_id)}"
        )


    elif text == "ℹ️ Help":

        await help_cmd(
            update,
            context
        )


# =========================
# ALL CALLBACK HANDLER
# =========================

async def all_callback(
    update,
    context
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        if query.data.startswith(
            (
                "check_task_"
            )
        ):

            await task_callback(
                update,
                context
            )

        else:

            await query.answer(
                "❌ Unauthorized!",
                show_alert=True
            )

        return


    data = query.data


    # USER TASK CHECK

    if data.startswith(
        "check_task_"
    ):

        await task_callback(
            update,
            context
        )

        return


    # DELETE TASK

    if data.startswith(
        "delete_task_"
    ):

        await query.answer()

        try:

            task_id = int(
                data.replace(
                    "delete_task_",
                    ""
                )
            )

        except ValueError:

            return

        delete_channel_task(
            task_id
        )

        await query.edit_message_text(
            "✅ TASK DELETED SUCCESSFULLY!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Task Manager",
                        callback_data="manage_tasks"
                    )
                ]
            ])
        )

        return


    # USER ADD POINTS

    if data.startswith(
        "user_add_"
    ):

        await query.answer()

        try:

            user_id = int(
                data.replace(
                    "user_add_",
                    ""
                )
            )

        except ValueError:

            return

        context.user_data[
            "points_action"
        ] = "add"

        context.user_data[
            "points_user_id"
        ] = user_id

        await query.message.reply_text(
            "➕ ADD POINTS\n\n"
            "কত Points add করতে চাও?\n\n"
            "Example: 100"
        )

        return


    # USER REMOVE POINTS

    if data.startswith(
        "user_remove_"
    ):

        await query.answer()

        try:

            user_id = int(
                data.replace(
                    "user_remove_",
                    ""
                )
            )

        except ValueError:

            return

        context.user_data[
            "points_action"
        ] = "remove"

        context.user_data[
            "points_user_id"
        ] = user_id

        await query.message.reply_text(
            "➖ REMOVE POINTS\n\n"
            "কত Points remove করতে চাও?\n\n"
            "Example: 100"
        )

        return


    # APPROVE

    if data.startswith(
        "approve_"
    ):

        await query.answer()

        try:

            withdrawal_id = int(
                data.replace(
                    "approve_",
                    ""
                )
            )

        except ValueError:

            return

        await approve_withdrawal(
            update,
            context,
            withdrawal_id
        )

        return


    # REJECT

    if data.startswith(
        "reject_"
    ):

        await query.answer()

        try:

            withdrawal_id = int(
                data.replace(
                    "reject_",
                    ""
                )
            )

        except ValueError:

            return

        await reject_withdrawal(
            update,
            context,
            withdrawal_id
        )

        return


    # WITHDRAW VIEW

    if data.startswith(
        "withdraw_view_"
    ):

        await query.answer()

        try:

            withdrawal_id = int(
                data.replace(
                    "withdraw_view_",
                    ""
                )
            )

        except ValueError:

            return

        await withdrawal_view(
            update,
            context,
            withdrawal_id
        )

        return


    # ADMIN HOME

    if data == "admin_home":

        await query.answer()

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📋 Manage Tasks",
                    callback_data="manage_tasks"
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Total Users",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 User Management",
                    callback_data="user_management"
                )
            ],
            [
                InlineKeyboardButton(
                    "📋 Users List",
                    callback_data="users_list"
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
                    "📊 Statistics",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    "📢 Broadcast",
                    callback_data="admin_broadcast"
                )
            ]
        ])

        await query.edit_message_text(
            "👑 ADMIN PANEL\n\n"
            "TaskMint Control Center",
            reply_markup=keyboard
        )

        return


    # MANAGE TASKS

    if data == "manage_tasks":

        await query.answer()

        await task_manager_menu(
            update,
            context
        )

        return


    # ADD TASK

    if data == "task_add":

        await query.answer()

        await add_task_start(
            update,
            context
        )

        return


    # TASK LIST

    if data == "task_list":

        await query.answer()

        await task_list(
            update,
            context
        )

        return


    # DELETE TASK MENU

    if data == "task_delete":

        await query.answer()

        await task_delete_menu(
            update,
            context
        )

        return


    # TOTAL USERS

    if data == "admin_users":

        await admin_users(
            update,
            context
        )

        return


    # USER MANAGEMENT

    if data == "user_management":

        await user_management(
            update,
            context
        )

        return


    # USERS LIST

    if data == "users_list":

        await users_list(
            update,
            context
        )

        return


    # STATISTICS

    if data == "admin_stats":

        await admin_statistics(
            update,
            context
        )

        return


    # PENDING WITHDRAWALS

    if data == "admin_withdrawals":

        await admin_withdrawals(
            update,
            context
        )

        return


    # BROADCAST

    if data == "admin_broadcast":

        await broadcast_start(
            update,
            context
        )

        return


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable "
            "is not set."
        )

    if not ADMIN_ID:

        raise ValueError(
            "ADMIN_ID environment variable "
            "is not set."
        )


    # DATABASE

    init_db()

    create_default_task()


    # RENDER HEALTH SERVER

    threading.Thread(
        target=web_server,
        daemon=True
    ).start()


    # APPLICATION

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )


    # START

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    # HELP

    app.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )


    # ADMIN

    app.add_handler(
        CommandHandler(
            "admin",
            admin_panel
        )
    )


    # WITHDRAW CONVERSATION

    withdraw_conversation = (
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex(
                        r"^💳 Withdraw$"
                    ),
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

            fallbacks=[]
        )
    )

    app.add_handler(
        withdraw_conversation
    )


    # ALL CALLBACKS

    app.add_handler(
        CallbackQueryHandler(
            all_callback
        )
    )


    # ADMIN TEXT

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            admin_text_handler
        ),
        group=1
    )


    # NORMAL TEXT

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            button_handler
        ),
        group=2
    )


    print(
        "=============================="
    )

    print(
        "TaskMint Bot is running..."
    )

    print(
        "=============================="
    )


    app.run_polling()


# =========================
# START BOT
# =========================

if __name__ == "__main__":

    main()
