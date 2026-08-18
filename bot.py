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

ADMIN_ID = 7003609983

CHANNEL_USERNAME = "@Amir10m300"
CHANNEL_URL = "https://t.me/Amir10m300"

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100

DB_FILE = "taskmint.db"

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
    conn = sqlite3.connect(DB_FILE)
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
# USER FUNCTIONS
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

            ref_exists = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (referrer,)
            ).fetchone()

            if ref_exists:
                valid_referrer = referrer

        conn.execute("""
        INSERT INTO users
        (user_id, username, first_name, referred_by)
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
        SET username=?, first_name=?
        WHERE user_id=?
        """, (
            user.username or "",
            user.first_name or "",
            user.id
        ))

    conn.commit()
    conn.close()


def points(uid):

    conn = db()

    row = conn.execute(
        "SELECT points FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()

    conn.close()

    if row:
        return row["points"]

    return 0


def add_points(uid, amount):

    conn = db()

    conn.execute("""
    UPDATE users
    SET points = points + ?
    WHERE user_id=?
    """, (
        amount,
        uid
    ))

    conn.commit()
    conn.close()


def remove_points(uid, amount):

    conn = db()

    conn.execute("""
    UPDATE users
    SET points = MAX(points - ?, 0)
    WHERE user_id=?
    """, (
        amount,
        uid
    ))

    conn.commit()
    conn.close()


# =========================
# TASK FUNCTIONS
# =========================

def task_done(uid, task_id):

    conn = db()

    row = conn.execute("""
    SELECT 1
    FROM tasks
    WHERE user_id=? AND task_id=?
    """, (
        uid,
        str(task_id)
    )).fetchone()

    conn.close()

    return row is not None


def save_task(uid, task_id):

    conn = db()

    conn.execute("""
    INSERT OR IGNORE INTO tasks
    (user_id, task_id)
    VALUES (?, ?)
    """, (
        uid,
        str(task_id)
    ))

    conn.commit()
    conn.close()


def create_default_task():

    conn = db()

    row = conn.execute("""
    SELECT id
    FROM channel_tasks
    WHERE channel=?
    """, (
        CHANNEL_USERNAME,
    )).fetchone()

    if not row:

        conn.execute("""
        INSERT INTO channel_tasks
        (channel, channel_url, title, reward, active)
        VALUES (?, ?, ?, ?, 1)
        """, (
            CHANNEL_USERNAME,
            CHANNEL_URL,
            "📢 Join Channel",
            TASK_REWARD
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
    (channel, channel_url, title, reward, active)
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
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"TaskMint Bot is running!"
        )

    def log_message(self, format, *args):
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
        except:
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


async def help_cmd(update, context):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন\n"
        "/admin - Admin Panel\n\n"
        "Task complete করে Points earn করো।"
    )


# =========================
# EARN TASKS
# =========================

async def earn_tasks(update, context):

    rows = get_channel_tasks()

    if not rows:

        await update.message.reply_text(
            "💰 Earn Tasks\n\n"
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
        "প্রথমে Channel Join করো।\n"
        "তারপর Check চাপো।\n\n"
        "প্রতিটি Task একবারই reward দেবে।",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )
# =========================
# TASK CHECK
# =========================

async def task_callback(update, context):

    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if not data.startswith("check_task_"):
        return

    try:
        task_id = int(
            data.split("_")[2]
        )
    except:
        return

    task = get_channel_task(task_id)

    if not task:

        await query.edit_message_text(
            "❌ এই Task আর available নেই।"
        )

        return

    if task_done(
        user_id,
        f"channel_{task_id}"
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
                f"channel_{task_id}"
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
            "Bot-কে ওই Channel-এর Admin করা হয়েছে কিনা "
            "চেক করো।"
        )


# =========================
# DAILY BONUS
# =========================

async def daily_bonus(update, context):

    user_id = update.effective_user.id

    conn = db()

    row = conn.execute("""
    SELECT last_bonus
    FROM users
    WHERE user_id=?
    """, (
        user_id,
    )).fetchone()

    today = datetime.utcnow().strftime(
        "%Y-%m-%d"
    )

    if row and row["last_bonus"] == today:

        conn.close()

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "⚠️ আজকের Bonus তুমি already নিয়েছো।\n\n"
            "আগামীকাল আবার নিতে পারবে।"
        )

        return

    conn.execute("""
    UPDATE users
    SET points=points+?,
        last_bonus=?
    WHERE user_id=?
    """, (
        DAILY_REWARD,
        today,
        user_id
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎉 Daily Bonus Received!\n\n"
        f"🎁 +{DAILY_REWARD} Points\n"
        f"💰 Total: {points(user_id)}"
    )


# =========================
# REFERRAL
# =========================

async def referral(update, context):

    user_id = update.effective_user.id

    conn = db()

    total = conn.execute("""
    SELECT COUNT(*)
    FROM users
    WHERE referred_by=?
    """, (
        user_id,
    )).fetchone()[0]

    conn.close()

    bot_username = context.bot.username

    link = (
        f"https://t.me/{bot_username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        "👥 REFER & EARN\n\n"
        "বন্ধুদের invite করে Points earn করো!\n\n"
        f"🎁 প্রতি Referral: +{REFERRAL_REWARD} Points\n"
        f"👤 Total Referrals: {total}\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{link}"
    )


# =========================
# WITHDRAW START
# =========================

async def withdraw_start(update, context):

    user_id = update.effective_user.id
    balance = points(user_id)

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            "💳 WITHDRAW\n\n"
            f"💰 Your Points: {balance}\n"
            f"⚠️ Minimum: {MIN_WITHDRAW} Points\n\n"
            "আরও Points earn করো।"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 WITHDRAW\n\n"
        f"💰 Available: {balance} Points\n"
        f"⚠️ Minimum: {MIN_WITHDRAW} Points\n\n"
        "কত Points withdraw করতে চাও?\n"
        "শুধু সংখ্যা পাঠাও।"
    )

    return AMOUNT


# =========================
# WITHDRAW AMOUNT
# =========================

async def withdraw_amount(update, context):

    user_id = update.effective_user.id
    balance = points(user_id)

    try:

        amount = int(
            update.message.text.strip()
        )

    except:

        await update.message.reply_text(
            "❌ শুধু সংখ্যা পাঠাও।\n\n"
            "Example: 100"
        )

        return AMOUNT

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            f"❌ Minimum {MIN_WITHDRAW} Points।"
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

async def withdraw_method(update, context):

    method = update.message.text.strip()

    if method not in (
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad"
    ):

        await update.message.reply_text(
            "❌ একটি valid payment method নির্বাচন করো।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
        "📱 এখন তোমার payment account number / ID পাঠাও।"
    )

    return ACCOUNT


# =========================
# WITHDRAW ACCOUNT
# =========================

async def withdraw_account(update, context):

    account = update.message.text.strip()

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

        return ConversationHandler.END

    remove_points(
        user_id,
        amount
    )

    conn = db()

    cur = conn.execute("""
    INSERT INTO withdrawals
    (user_id,username,amount,method,account,status)
    VALUES(?,?,?,?,?,'pending')
    """, (
        user_id,
        username,
        amount,
        method,
        account
    ))

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
                callback_data=f"approve_{withdrawal_id}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{withdrawal_id}"
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
        "⏳ Admin verification-এর জন্য অপেক্ষা করো।",
        reply_markup=MARKUP
    )

    context.user_data.clear()

    return ConversationHandler.END


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

async def task_manager_menu(update, context):

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
        "এখান থেকে Channel Task manage করতে পারবে।",
        reply_markup=keyboard
    )


# =========================
# ADD TASK START
# =========================

async def add_task_start(update, context):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

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

async def task_list(update, context):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    rows = get_channel_tasks()

    if not rows:

        text = "📋 Active Tasks\n\nকোনো Task নেই।"

    else:

        text = "📋 ACTIVE TASKS\n\n"

        for row in rows:

            text += (
                f"🆔 ID: {row['id']}\n"
                f"📢 {row['channel']}\n"
                f"📝 {row['title']}\n"
                f"💰 Reward: {row['reward']} Points\n\n"
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
# TASK DELETE MENU
# =========================

async def task_delete_menu(update, context):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    rows = get_channel_tasks()

    if not rows:

        await query.edit_message_text(
            "🗑️ Delete Task\n\n"
            "কোনো active task নেই।"
        )
        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"🗑️ #{row['id']} {row['title']}",
                callback_data=f"delete_task_{row['id']}"
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
        "যে Task delete করতে চাও সেটি নির্বাচন করো:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(update, context):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized!",
            show_alert=True
        )
        return

    await query.answer()

    data = query.data

    # Task Manager
    if data == "manage_tasks":

        await task_manager_menu(
            update,
            context
        )
        return

    if data == "task_add":

        await add_task_start(
            update,
            context
        )
        return

    if data == "task_list":

        await task_list(
            update,
            context
        )
        return

    if data == "task_delete":

        await task_delete_menu(
            update,
            context
        )
        return

    if data.startswith("delete_task_"):

        task_id = int(
            data.split("_")[2]
        )

        delete_channel_task(task_id)

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

    # Admin Home
    if data == "admin_home":

        await admin_panel(
            update,
            context
        )
        return

    # Total Users
    if data == "admin_users":

        conn = db()

        total = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        conn.close()

        await query.edit_message_text(
            f"👥 Total Users: {total}",
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

    # Statistics
    if data == "admin_stats":

        conn = db()

        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total_points = conn.execute(
            "SELECT COALESCE(SUM(points),0) FROM users"
        ).fetchone()[0]

        pending = conn.execute("""
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status='pending'
        """).fetchone()[0]

        conn.close()

        await query.edit_message_text(
            "📊 BOT STATISTICS\n\n"
            f"👥 Users: {users}\n"
            f"💰 Total Points: {total_points}\n"
            f"💳 Pending Withdrawals: {pending}",
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

    # Pending Withdrawals
    if data == "admin_withdrawals":

        conn = db()

        rows = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

        conn.close()

        if not rows:

            text = (
                "💳 Pending Withdrawals\n\n"
                "No pending withdrawals."
            )

        else:

            text = "💳 PENDING WITHDRAWALS\n\n"

            for row in rows:

                text += (
                    f"#{row['id']} - "
                    f"{row['amount']} Points - "
                    f"{row['method']}\n"
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

        return

    # Broadcast
    if data == "admin_broadcast":

        context.user_data[
            "broadcast"
        ] = True

        await query.edit_message_text(
            "📢 BROADCAST\n\n"
            "এখন যে message সবাইকে পাঠাতে চাও "
            "সেটা পাঠাও।"
        )

        return

    # Approve
    if data.startswith("approve_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        conn = db()

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

        if not row:

            conn.close()

            await query.edit_message_text(
                "❌ Withdrawal not found."
            )
            return

        if row["status"] != "pending":

            conn.close()

            await query.answer(
                "Already processed.",
                show_alert=True
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status='approved'
            WHERE id=?
        """, (
            withdrawal_id,
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            query.message.text +
            "\n\n✅ STATUS: APPROVED"
        )

        try:

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "✅ Withdrawal Approved!\n\n"
                    f"💰 Amount: {row['amount']} Points\n"
                    f"💳 Method: {row['method']}\n\n"
                    "Admin তোমার request approve করেছে।"
                )
            )

        except Exception as e:

            print(
                "Approve notification error:",
                e
            )

        return

    # Reject
    if data.startswith("reject_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        conn = db()

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

        if not row:

            conn.close()

            await query.edit_message_text(
                "❌ Withdrawal not found."
            )
            return

        if row["status"] != "pending":

            conn.close()

            await query.answer(
                "Already processed.",
                show_alert=True
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status='rejected'
            WHERE id=?
        """, (
            withdrawal_id,
        ))

        conn.execute("""
            UPDATE users
            SET points=points+?
            WHERE user_id=?
        """, (
            row["amount"],
            row["user_id"]
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            query.message.text +
            "\n\n❌ STATUS: REJECTED\n"
            f"↩️ {row['amount']} Points returned."
        )

        try:

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "❌ Withdrawal Rejected\n\n"
                    f"↩️ {row['amount']} Points "
                    "তোমার balance-এ ফেরত দেওয়া হয়েছে।"
                )
            )

        except Exception as e:

            print(
                "Reject notification error:",
                e
            )

        return


# =========================
# ADMIN TEXT INPUT
# =========================

async def admin_text_handler(update, context):

    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.strip()

    step = context.user_data.get(
        "task_add_step"
    )

    # Channel
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

    # URL
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
            "📢 Join My Channel"
        )

        return

    # Title
    if step == "title":

        context.user_data[
            "new_task_title"
        ] = text

        context.user_data[
            "task_add_step"
        ] = "reward"

        await update.message.reply_text(
            "💰 এখন কত Points reward দিতে চাও?\n\n"
            "Example: 10"
        )

        return

    # Reward
    if step == "reward":

        try:

            reward = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Reward শুধু সংখ্যা হতে হবে।\n\n"
                "Example: 10"
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

        url = context.user_data[
            "new_channel_url"
        ]

        title = context.user_data[
            "new_task_title"
        ]

        add_channel_task(
            channel,
            url,
            title,
            reward
        )

        context.user_data.pop(
            "task_add_step",
            None
        )

        context.user_data.pop(
            "new_channel",
            None
        )

        context.user_data.pop(
            "new_channel_url",
            None
        )

        context.user_data.pop(
            "new_task_title",
            None
        )

        await update.message.reply_text(
            "🎉 TASK ADDED SUCCESSFULLY!\n\n"
            f"📢 Channel: {channel}\n"
            f"📝 Title: {title}\n"
            f"💰 Reward: {reward} Points",
            reply_markup=MARKUP
        )

        return


# =========================
# BROADCAST
# =========================

async def broadcast_handler(update, context):

    if update.effective_user.id != ADMIN_ID:
        return

    if not context.user_data.get(
        "broadcast"
    ):
        return

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
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )

            sent += 1

        except Exception:

            failed += 1

    await update.message.reply_text(
        "📢 Broadcast Finished!\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


# =========================
# NORMAL BUTTONS
# =========================

async def button_handler(update, context):

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

        user_id = update.effective_user.id

        await update.message.reply_text(
            "📊 MY BALANCE\n\n"
            f"💰 Points: {points(user_id)}"
        )

    elif text == "ℹ️ Help":

        await help_cmd(
            update,
            context
        )


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:

        raise ValueError(
            "BOT_TOKEN is not set"
        )

    init_db()

    create_default_task()

    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # Commands

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin_panel
        )
    )

    # Withdraw

    withdraw_conversation = ConversationHandler(

        entry_points=[
            MessageHandler(
                filters.Regex(
                    "^💳 Withdraw$"
                ),
                withdraw_start
            )
        ],

        states={

            AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_amount
                )
            ],

            METHOD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_method
                )
            ],

            ACCOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    withdraw_account
                )
            ],
        },

        fallbacks=[]
    )

    app.add_handler(
        withdraw_conversation
    )

    # Task callbacks

    app.add_handler(
        CallbackQueryHandler(
            task_callback,
            pattern=r"^check_task_"
        )
    )

    # Admin callbacks

    app.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^(manage_tasks|task_|admin_|approve_|reject_)"
        )
    )

    # Admin text input
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            admin_text_handler
        ),
        group=1
    )

    # Broadcast
    app.add_handler(
        MessageHandler(
            filters.ALL,
            broadcast_handler
        ),
        group=2
    )

    # Normal buttons
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            button_handler
        ),
        group=3
    )

    print(
        "TaskMint Bot is running..."
    )

    app.run_polling()


if __name__ == "__main__":
    main()
