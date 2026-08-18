import os
import threading
import sqlite3
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
MIN_WITHDRAW = 100

DB_FILE = "taskmint.db"

AMOUNT, METHOD, ACCOUNT = range(3)


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            points INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            task_id TEXT,
            PRIMARY KEY (user_id, task_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    conn.commit()
    conn.close()


def register_user(user):

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, first_name, points)
        VALUES (?, ?, ?, 0)
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
    ))

    conn.execute("""
        UPDATE users
        SET username = ?, first_name = ?
        WHERE user_id = ?
    """, (
        user.username or "",
        user.first_name or "",
        user.id,
    ))

    conn.commit()
    conn.close()


def get_points(user_id):

    conn = get_db()

    row = conn.execute(
        "SELECT points FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    if row:
        return row["points"]

    return 0


def add_points(user_id, amount):

    conn = get_db()

    conn.execute(
        "UPDATE users SET points = points + ? WHERE user_id = ?",
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def remove_points(user_id, amount):

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET points = MAX(points - ?, 0)
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


def task_completed(user_id, task_id):

    conn = get_db()

    row = conn.execute("""
        SELECT 1
        FROM completed_tasks
        WHERE user_id = ? AND task_id = ?
    """, (
        user_id,
        task_id
    )).fetchone()

    conn.close()

    return row is not None


def mark_task_completed(user_id, task_id):

    conn = get_db()

    conn.execute("""
        INSERT OR IGNORE INTO completed_tasks
        (user_id, task_id)
        VALUES (?, ?)
    """, (
        user_id,
        task_id
    ))

    conn.commit()
    conn.close()


# =========================
# RENDER SERVER
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


def start_web_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    server.serve_forever()


# =========================
# MAIN MENU
# =========================

keyboard = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 Refer করে Points earn করতে পারবে।\n"
        "💳 Points দিয়ে Withdraw করতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# HELP
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন\n\n"
        "সমস্যা হলে Admin-এর সাথে যোগাযোগ করুন।"
    )


# =========================
# EARN TASKS
# =========================

async def earn_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_URL
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Check Task",
                callback_data="check_join"
            )
        ]
    ]

    await update.message.reply_text(
        "💰 Earn Tasks\n\n"
        "📢 Join our Telegram Channel\n\n"
        f"💰 Reward: +{TASK_REWARD} Points\n\n"
        "1️⃣ Join Channel চাপো\n"
        "2️⃣ Channel-এ Join করো\n"
        "3️⃣ তারপর ✅ Check Task চাপো",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )
# =========================
# WITHDRAW
# =========================

async def withdraw_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    points = get_points(user_id)

    if points < MIN_WITHDRAW:

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            f"💰 Your Points: {points}\n"
            f"⚠️ Minimum Withdrawal: {MIN_WITHDRAW} Points\n\n"
            "আরও Points earn করো।"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 Withdrawal\n\n"
        f"💰 Available Points: {points}\n"
        f"⚠️ Minimum: {MIN_WITHDRAW} Points\n\n"
        "কত Points withdraw করতে চাও?\n"
        "শুধু সংখ্যা লিখো।"
    )

    return AMOUNT


async def withdraw_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    points = get_points(user_id)

    try:
        amount = int(update.message.text.strip())
    except ValueError:

        await update.message.reply_text(
            "❌ শুধু সংখ্যা লিখো।\n\n"
            "উদাহরণ: 100"
        )

        return AMOUNT

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            f"❌ Minimum Withdrawal "
            f"{MIN_WITHDRAW} Points।"
        )

        return AMOUNT

    if amount > points:

        await update.message.reply_text(
            "❌ তোমার কাছে এত Points নেই!\n\n"
            f"💰 Available: {points}"
        )

        return AMOUNT

    context.user_data["withdraw_amount"] = amount

    keyboard = [
        ["💰 Binance"],
        ["📱 bKash"],
        ["📱 Nagad"],
    ]

    await update.message.reply_text(
        "💳 Payment Method নির্বাচন করো:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return METHOD


async def withdraw_method(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    method = update.message.text.strip()

    allowed = [
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad",
    ]

    if method not in allowed:

        await update.message.reply_text(
            "❌ একটি valid payment method "
            "নির্বাচন করো।"
        )

        return METHOD

    context.user_data["withdraw_method"] = method

    await update.message.reply_text(
        "📱 এখন তোমার payment "
        "account number / ID পাঠাও।"
    )

    return ACCOUNT


async def withdraw_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    account = update.message.text.strip()

    if len(account) < 4:

        await update.message.reply_text(
            "❌ সঠিক account number / ID দাও।"
        )

        return ACCOUNT

    user_id = update.effective_user.id
    username = update.effective_user.username or "No Username"

    amount = context.user_data["withdraw_amount"]
    method = context.user_data["withdraw_method"]

    points = get_points(user_id)

    if amount > points:

        await update.message.reply_text(
            "❌ Balance পরিবর্তিত হয়েছে। "
            "আবার চেষ্টা করো।",
            reply_markup=reply_markup
        )

        context.user_data.clear()

        return ConversationHandler.END

    remove_points(user_id, amount)

    conn = get_db()

    cur = conn.execute("""
        INSERT INTO withdrawals
        (user_id, username, amount, method, account, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
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
        f"🆔 Request ID: #{withdrawal_id}\n"
        f"👤 User ID: {user_id}\n"
        f"👤 Username: @{username}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n"
        f"💰 Remaining: {get_points(user_id)} Points"
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
        reply_markup=reply_markup
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Unauthorized."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 Total Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Pending Withdrawals",
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
        ],
    ]

    await update.message.reply_text(
        "👑 ADMIN PANEL\n\n"
        "শুধু Admin-এর জন্য।",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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

    if data == "admin_users":

        conn = get_db()

        row = conn.execute(
            "SELECT COUNT(*) AS total FROM users"
        ).fetchone()

        conn.close()

        await query.edit_message_text(
            f"👥 Total Users: {row['total']}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin_home"
                    )
                ]
            ])
        )

    elif data == "admin_stats":

        conn = get_db()

        users = conn.execute(
            "SELECT COUNT(*) AS total FROM users"
        ).fetchone()["total"]

        points = conn.execute(
            "SELECT COALESCE(SUM(points), 0) AS total "
            "FROM users"
        ).fetchone()["total"]

        pending = conn.execute("""
            SELECT COUNT(*) AS total
            FROM withdrawals
            WHERE status = 'pending'
        """).fetchone()["total"]

        conn.close()

        await query.edit_message_text(
            "📊 BOT STATISTICS\n\n"
            f"👥 Users: {users}\n"
            f"💰 Total Points: {points}\n"
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

    elif data == "admin_withdrawals":

        conn = get_db()

        rows = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE status = 'pending'
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
                    f"#{row['id']} — "
                    f"{row['amount']} Points — "
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

    elif data == "admin_broadcast":

        context.user_data["broadcast_mode"] = True

        await query.edit_message_text(
            "📢 BROADCAST\n\n"
            "এখন যে message সবাইকে পাঠাতে চাও "
            "সেটা পাঠাও।"
        )

    elif data == "admin_home":

        keyboard = [
            [
                InlineKeyboardButton(
                    "👥 Total Users",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    "💰 Pending Withdrawals",
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
            ],
        ]

        await query.edit_message_text(
            "👑 ADMIN PANEL\n\n"
            "Admin control center",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    elif data.startswith("approve_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        conn = get_db()

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
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
            SET status = 'approved'
            WHERE id = ?
        """, (
            withdrawal_id,
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            "✅ STATUS: APPROVED"
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
                "User notification error:",
                e
            )

    elif data.startswith("reject_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        conn = get_db()

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
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
            SET status = 'rejected'
            WHERE id = ?
        """, (
            withdrawal_id,
        ))

        conn.execute("""
            UPDATE users
            SET points = points + ?
            WHERE user_id = ?
        """, (
            row["amount"],
            row["user_id"],
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            "❌ STATUS: REJECTED\n"
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
                "User notification error:",
                e
        )
