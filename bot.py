import os
import sqlite3
import threading
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
    filters,
)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")

PORT = int(os.getenv("PORT", "10000"))

ADMIN_ID = 7003609983

CHANNEL_USERNAME = "@Amir10m300"

CHANNEL_LINK = "https://t.me/Amir10m300"

DB_FILE = "taskmint.db"


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            points INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            points INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def add_user(user_id, username, first_name):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cursor.fetchone()

    if not exists:
        cursor.execute(
            """
            INSERT INTO users
            (user_id, username, first_name, points)
            VALUES (?, ?, ?, 0)
            """,
            (user_id, username, first_name)
        )

    else:
        cursor.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (username, first_name, user_id)
        )

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    user = cursor.fetchone()

    conn.close()

    return user


def get_points(user_id):
    user = get_user(user_id)

    if user:
        return user["points"]

    return 0


def add_points(user_id, amount):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id = ?
        """,
        (amount, user_id)
    )

    conn.commit()
    conn.close()


def remove_points(user_id, amount):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET points = points - ?
        WHERE user_id = ?
        AND points >= ?
        """,
        (amount, user_id, amount)
    )

    changed = cursor.rowcount

    conn.commit()
    conn.close()

    return changed > 0


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        return


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# =========================
# KEYBOARDS
# =========================

def main_menu():
    keyboard = [
        ["💰 Earn Tasks", "👥 Refer & Earn"],
        ["🎁 Daily Bonus", "💳 My Balance"],
        ["💸 Withdraw", "📊 Statistics"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


def back_menu():
    keyboard = [
        ["🔙 Back to Menu"]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    add_user(
        user.id,
        user.username,
        user.first_name
    )

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🚀 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn points.\n"
        "👥 Invite friends and earn rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💸 Withdraw your earnings.\n\n"
        "👇 Choose an option:",
        reply_markup=main_menu()
    )


# =========================
# BASIC COMMAND
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "💰 Earn points by completing tasks.\n"
        "👥 Invite friends to earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Check your balance.\n"
        "💸 Withdraw your points.",
        reply_markup=main_menu()
    )


# =========================
# INIT DATABASE
# =========================

init_db()
# =========================
# EARN TASKS
# =========================

async def earn_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Check & Earn",
                callback_data="check_channel"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_menu"
            )
        ]
    ]

    await update.message.reply_text(
        "💰 EARN TASKS\n\n"
        "📢 Task: Join our Telegram channel\n\n"
        f"🔗 Channel: {CHANNEL_USERNAME}\n\n"
        "1️⃣ Join the channel\n"
        "2️⃣ Click 'Check & Earn'\n"
        "3️⃣ If you joined successfully, points will be added.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# CHECK CHANNEL
# =========================

async def check_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    try:

        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id
        )

        status = member.status

        if status in ["member", "administrator", "creator"]:

            conn = get_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT points
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            user = cursor.fetchone()

            if not user:
                conn.close()

                add_user(
                    user_id,
                    query.from_user.username,
                    query.from_user.first_name
                )

                user_points = 0

            else:
                user_points = user["points"]
                conn.close()

            # Prevent earning repeatedly
            context.user_data.setdefault(
                "completed_tasks",
                set()
            )

            if "channel_join" in context.user_data["completed_tasks"]:

                await query.message.reply_text(
                    "⚠️ You have already completed this task."
                )

                return

            add_points(user_id, 10)

            context.user_data["completed_tasks"].add(
                "channel_join"
            )

            await query.message.reply_text(
                "🎉 Task Completed!\n\n"
                "✅ Channel join verified.\n"
                "💰 +10 points added!\n\n"
                f"💳 Your balance: {get_points(user_id)} points"
            )

        else:

            await query.message.reply_text(
                "❌ You haven't joined the channel yet.\n\n"
                "Please join the channel first, "
                "then click Check & Earn."
            )

    except Exception as e:

        print("Channel check error:", e)

        await query.message.reply_text(
            "⚠️ Unable to verify your membership right now.\n"
            "Please make sure you joined the channel and try again."
        )


# =========================
# MY BALANCE
# =========================

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    points = get_points(user_id)

    await update.message.reply_text(
        "💳 MY BALANCE\n\n"
        f"💰 Points: {points}\n\n"
        "Keep completing tasks to earn more!",
        reply_markup=back_menu()
    )


# =========================
# STATISTICS
# =========================

async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) AS total FROM users"
    )

    result = cursor.fetchone()

    total_users = result["total"]

    cursor.execute(
        "SELECT COALESCE(SUM(points), 0) AS total_points FROM users"
    )

    result = cursor.fetchone()

    total_points = result["total_points"]

    conn.close()

    await update.message.reply_text(
        "📊 TASKMINT STATISTICS\n\n"
        f"👥 Total Users: {total_users}\n"
        f"💰 Total Points: {total_points}",
        reply_markup=back_menu()
    )


# =========================
# WITHDRAW START
# =========================

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    points = get_points(user_id)

    if points < 100:

        await update.message.reply_text(
            "❌ Minimum withdrawal is 100 points.\n\n"
            f"💰 Your current balance: {points} points",
            reply_markup=main_menu()
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "💎 Binance",
                callback_data="withdraw_binance"
            )
        ],
        [
            InlineKeyboardButton(
                "📱 Telegram",
                callback_data="withdraw_telegram"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_menu"
            )
        ]
    ]

    await update.message.reply_text(
        "💸 WITHDRAW\n\n"
        f"💰 Available points: {points}\n\n"
        "Select your withdrawal method:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# WITHDRAW METHOD
# =========================

async def withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    method = query.data.replace(
        "withdraw_",
        ""
    )

    context.user_data["withdraw_method"] = method

    await query.message.reply_text(
        f"💸 Withdrawal Method: {method.title()}\n\n"
        "Please enter the account/address where you want "
        "to receive your payment."
    )

    context.user_data["awaiting_withdraw_account"] = True


# =========================
# PROCESS WITHDRAW
# =========================

async def process_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.user_data.get(
        "awaiting_withdraw_account"
    ):
        return False

    account = update.message.text.strip()

    user = update.effective_user

    user_id = user.id

    method = context.user_data.get(
        "withdraw_method",
        "unknown"
    )

    points = get_points(user_id)

    if points < 100:

        context.user_data.pop(
            "awaiting_withdraw_account",
            None
        )

        await update.message.reply_text(
            "❌ You don't have enough points.",
            reply_markup=main_menu()
        )

        return True

    success = remove_points(
        user_id,
        points
    )

    if not success:

        await update.message.reply_text(
            "❌ Withdrawal failed. Please try again."
        )

        return True

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO withdrawals
        (user_id, username, points, method, account, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (
            user_id,
            user.username or "",
            points,
            method,
            account
        )
    )

    withdrawal_id = cursor.lastrowid

    conn.commit()
    conn.close()

    context.user_data.pop(
        "awaiting_withdraw_account",
        None
    )

    # Admin notification
    if ADMIN_ID:

        try:

            keyboard = [
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
            ]

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 NEW WITHDRAWAL\n\n"
                    f"🆔 ID: {withdrawal_id}\n"
                    f"👤 User ID: {user_id}\n"
                    f"📛 Username: @{user.username or 'N/A'}\n"
                    f"💰 Points: {points}\n"
                    f"💳 Method: {method.title()}\n"
                    f"📥 Account: {account}"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    await update.message.reply_text(
        "✅ Withdrawal request submitted!\n\n"
        f"🆔 Request ID: {withdrawal_id}\n"
        f"💰 Points: {points}\n"
        f"💳 Method: {method.title()}\n\n"
        "⏳ Your request is waiting for admin approval.",
        reply_markup=main_menu()
    )

    return True


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ You are not authorized."
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
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Pending Withdrawals",
                callback_data="admin_pending"
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
        "Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# ADMIN CALLBACKS
# =========================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_ID:

        await query.message.reply_text(
            "❌ Unauthorized."
        )

        return

    data = query.data

    conn = get_db()
    cursor = conn.cursor()

    if data == "admin_users":

        cursor.execute(
            "SELECT COUNT(*) AS total FROM users"
        )

        total = cursor.fetchone()["total"]

        await query.message.reply_text(
            f"👥 TOTAL USERS\n\n{total}"
        )

    elif data == "admin_stats":

        cursor.execute(
            "SELECT COUNT(*) AS total FROM users"
        )

        users = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COALESCE(SUM(points), 0) AS total FROM users"
        )

        points = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM withdrawals
            WHERE status = 'pending'
            """
        )

        pending = cursor.fetchone()["total"]

        await query.message.reply_text(
            "📊 ADMIN STATISTICS\n\n"
            f"👥 Users: {users}\n"
            f"💰 Total Points: {points}\n"
            f"⏳ Pending Withdrawals: {pending}"
        )

    elif data == "admin_pending":

        cursor.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE status = 'pending'
            ORDER BY id DESC
            LIMIT 20
            """
        )

        rows = cursor.fetchall()

        if not rows:

            await query.message.reply_text(
                "✅ No pending withdrawals."
            )

        else:

            text = "💸 PENDING WITHDRAWALS\n\n"

            for row in rows:

                text += (
                    f"🆔 {row['id']}\n"
                    f"👤 {row['user_id']}\n"
                    f"💰 {row['points']} points\n"
                    f"💳 {row['method']}\n"
                    f"📥 {row['account']}\n\n"
                )

            await query.message.reply_text(
                text
            )

    elif data == "admin_broadcast":

        context.user_data[
            "awaiting_broadcast"
        ] = True

        await query.message.reply_text(
            "📢 BROADCAST\n\n"
            "Send the message you want to broadcast."
        )

    conn.close()
