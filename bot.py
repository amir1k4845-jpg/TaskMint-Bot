import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TOKEN:
    raise ValueError("BOT_TOKEN is not set")

if ADMIN_ID:
    ADMIN_ID = int(ADMIN_ID)

PORT = int(os.getenv("PORT", "10000"))
DB_NAME = "taskmint.db"

# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(DB_NAME, check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    points INTEGER DEFAULT 0,
    referrals INTEGER DEFAULT 0,
    referred_by INTEGER DEFAULT NULL,
    daily_claim TEXT DEFAULT '',
    joined_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    amount INTEGER,
    method TEXT,
    account TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    reward INTEGER,
    link TEXT,
    active INTEGER DEFAULT 1
)
""")

db.commit()

# =========================================================
# HELPERS
# =========================================================

def get_user(user_id):
    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )
    return cursor.fetchone()


def create_user(user_id, username, first_name, referred_by=None):
    if get_user(user_id):
        return

    cursor.execute("""
        INSERT INTO users
        (user_id, username, first_name, referred_by)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        username or "",
        first_name or "",
        referred_by
    ))

    db.commit()

    # Referral reward
    if referred_by and referred_by != user_id:
        referrer = get_user(referred_by)

        if referrer:
            cursor.execute("""
                UPDATE users
                SET points = points + 10,
                    referrals = referrals + 1
                WHERE user_id = ?
            """, (referred_by,))

            db.commit()


def add_points(user_id, amount):
    cursor.execute("""
        UPDATE users
        SET points = points + ?
        WHERE user_id = ?
    """, (amount, user_id))
    db.commit()


def get_points(user_id):
    user = get_user(user_id)
    if not user:
        return 0
    return user[3]


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        return


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=run_server,
    daemon=True
).start()

# =========================================================
# START COMMAND
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            referred_by = int(context.args[0])
        except:
            referred_by = None

    create_user(
        user.id,
        user.username,
        user.first_name,
        referred_by
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 Earn Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "👥 Refer & Earn",
                callback_data="refer"
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 Daily Bonus",
                callback_data="daily"
            ),
            InlineKeyboardButton(
                "💰 My Balance",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Withdrawal",
                callback_data="withdraw"
            )
        ]
    ]

    if ADMIN_ID and user.id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton(
                "👨‍💼 Admin Panel",
                callback_data="admin"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Welcome to TaskMint Bot, {user.first_name}!\n\n"
        "💰 Complete tasks and earn points.\n"
        "👥 Invite friends and earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💸 Withdraw your earned balance.\n\n"
        "👇 Choose an option:",
        reply_markup=reply_markup
    )


# =========================================================
# MAIN MENU
# =========================================================

async def main_menu(query):

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 Earn Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "👥 Refer & Earn",
                callback_data="refer"
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 Daily Bonus",
                callback_data="daily"
            ),
            InlineKeyboardButton(
                "💰 My Balance",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Withdrawal",
                callback_data="withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Home",
                callback_data="home"
            )
        ]
    ]

    if ADMIN_ID and query.from_user.id == ADMIN_ID:
        keyboard.insert(
            -1,
            [
                InlineKeyboardButton(
                    "👨‍💼 Admin Panel",
                    callback_data="admin"
                )
            ]
        )

    await query.edit_message_text(
        "🏠 **TaskMint Bot**\n\n"
        "Choose an option below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# TASKS
# =========================================================

async def show_tasks(query):

    cursor.execute("""
        SELECT id, title, reward, link
        FROM tasks
        WHERE active = 1
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    if not tasks:
        keyboard = [[
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ]]

        await query.edit_message_text(
            "🎯 **Earn Tasks**\n\n"
            "Currently no tasks are available.\n"
            "Please check again later.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    keyboard = []

    for task_id, title, reward, link in tasks:

        keyboard.append([
            InlineKeyboardButton(
                f"🎯 {title} +{reward} points",
                callback_data=f"task_{task_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="home"
        )
    ])

    await query.edit_message_text(
        "🎯 **Earn Tasks**\n\n"
        "Complete a task to earn points:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def open_task(query, task_id):

    cursor.execute("""
        SELECT id, title, reward, link
        FROM tasks
        WHERE id = ? AND active = 1
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await query.answer(
            "Task not found!",
            show_alert=True
        )
        return

    _, title, reward, link = task

    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Open Task",
                url=link
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Complete Task",
                callback_data=f"complete_{task_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="tasks"
            )
        ]
    ]

    await query.edit_message_text(
        f"🎯 **{title}**\n\n"
        f"💰 Reward: **{reward} points**\n\n"
        "1️⃣ Open the task.\n"
        "2️⃣ Complete it.\n"
        "3️⃣ Press Complete Task.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def complete_task(query, task_id):

    user_id = query.from_user.id

    cursor.execute("""
        SELECT reward
        FROM tasks
        WHERE id = ? AND active = 1
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await query.answer(
            "Task unavailable!",
            show_alert=True
        )
        return

    reward = task[0]

    # Simple task reward
    # Later we can add proof/verification.
    add_points(user_id, reward)

    await query.answer(
        f"🎉 +{reward} points added!",
        show_alert=True
    )

    await show_tasks(query)


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(query):

    user_id = query.from_user.id
    user = get_user(user_id)

    referrals = user[4] if user else 0

    bot_username = context_bot_username = (
        (await query.get_bot().get_me()).username
    )

    referral_link = (
        f"https://t.me/{bot_username}?start={user_id}"
    )

    keyboard = [[
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="home"
        )
    ]]

    await query.edit_message_text(
        "👥 **Refer & Earn**\n\n"
        f"👤 Your referrals: **{referrals}**\n"
        "💰 Reward per referral: **10 points**\n\n"
        "🔗 Your referral link:\n"
        f"`{referral_link}`\n\n"
        "Share this link with your friends!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# DAILY BONUS
# =========================================================

async def daily_bonus(query):

    user_id = query.from_user.id

    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")

    user = get_user(user_id)

    if user and user[5] == today:

        await query.answer(
            "❌ You already claimed today's bonus!",
            show_alert=True
        )
        return

    reward = 5

    cursor.execute("""
        UPDATE users
        SET points = points + ?,
            daily_claim = ?
        WHERE user_id = ?
    """, (reward, today, user_id))

    db.commit()

    await query.answer(
        f"🎁 +{reward} points received!",
        show_alert=True
    )

    await query.edit_message_text(
        "🎁 **Daily Bonus Claimed!**\n\n"
        f"💰 You received **{reward} points**.\n\n"
        "Come back tomorrow for another bonus.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏠 Home",
                    callback_data="home"
                )
            ]
        ]),
        parse_mode="Markdown"
    )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(query):

    user_id = query.from_user.id

    user = get_user(user_id)

    points = user[3] if user else 0
    referrals = user[4] if user else 0

    keyboard = [[
        InlineKeyboardButton(
            "💸 Withdraw",
            callback_data="withdraw"
        )
    ], [
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="home"
        )
    ]]

    await query.edit_message_text(
        "💰 **My Balance**\n\n"
        f"💎 Points: **{points}**\n"
        f"👥 Referrals: **{referrals}**\n\n"
        "Minimum withdrawal: **100 points**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# WITHDRAWAL START
# =========================================================

async def withdrawal_menu(query):

    user_id = query.from_user.id
    points = get_points(user_id)

    keyboard = [
        [
            InlineKeyboardButton(
                "💸 Request Withdrawal",
                callback_data="withdraw_request"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ]
    ]

    await query.edit_message_text(
        "💸 **Withdrawal**\n\n"
        f"💰 Your balance: **{points} points**\n\n"
        "Minimum withdrawal: **100 points**\n\n"
        "Press the button below to request a withdrawal.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
        )
    # =========================================================
# WITHDRAWAL REQUEST
# =========================================================

async def withdrawal_request(query, context):

    user_id = query.from_user.id
    points = get_points(user_id)

    if points < 100:
        await query.answer(
            "❌ Minimum 100 points required!",
            show_alert=True
        )
        return

    context.user_data["withdraw_step"] = "amount"

    await query.edit_message_text(
        "💸 **Withdrawal Request**\n\n"
        f"Your balance: **{points} points**\n\n"
        "Enter the amount of points you want to withdraw.\n\n"
        "Example: `100`",
        parse_mode="Markdown"
    )


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    text = update.message.text.strip()

    # -----------------------------------------------------
    # WITHDRAW AMOUNT
    # -----------------------------------------------------

    if context.user_data.get("withdraw_step") == "amount":

        try:
            amount = int(text)
        except ValueError:

            await update.message.reply_text(
                "❌ Please enter a valid number.\n\n"
                "Example: 100"
            )
            return

        balance = get_points(user.id)

        if amount < 100:
            await update.message.reply_text(
                "❌ Minimum withdrawal is 100 points."
            )
            return

        if amount > balance:
            await update.message.reply_text(
                "❌ You don't have enough points."
            )
            return

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "method"

        await update.message.reply_text(
            "💳 **Select payment method:**",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📱 bKash",
                        callback_data="method_bkash"
                    ),
                    InlineKeyboardButton(
                        "📱 Nagad",
                        callback_data="method_nagad"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Cancel",
                        callback_data="home"
                    )
                ]
            ]),
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------------------
    # PAYMENT ACCOUNT
    # -----------------------------------------------------

    if context.user_data.get("withdraw_step") == "account":

        method = context.user_data.get("withdraw_method")
        amount = context.user_data.get("withdraw_amount")

        account = text

        balance = get_points(user.id)

        if amount > balance:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Insufficient balance."
            )
            return

        # Deduct points
        cursor.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
        """, (amount, user.id))

        # Save withdrawal
        cursor.execute("""
            INSERT INTO withdrawals
            (user_id, username, amount, method, account)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username or "",
            amount,
            method,
            account
        ))

        db.commit()

        withdrawal_id = cursor.lastrowid

        context.user_data.clear()

        await update.message.reply_text(
            "✅ **Withdrawal Request Submitted!**\n\n"
            f"💰 Amount: **{amount} points**\n"
            f"💳 Method: **{method}**\n"
            f"📱 Account: `{account}`\n"
            f"🆔 Request ID: **#{withdrawal_id}**\n\n"
            "⏳ Your request is waiting for admin approval.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏠 Home",
                        callback_data="home"
                    )
                ]
            ])
        )

        # -------------------------------------------------
        # ADMIN NOTIFICATION
        # -------------------------------------------------

        if ADMIN_ID:

            try:

                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "🔔 **NEW WITHDRAWAL**\n\n"
                        f"🆔 Request: #{withdrawal_id}\n"
                        f"👤 User ID: `{user.id}`\n"
                        f"👤 Username: @{user.username or 'N/A'}\n"
                        f"💰 Amount: {amount} points\n"
                        f"💳 Method: {method}\n"
                        f"📱 Account: `{account}`"
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
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
                )

            except Exception as e:
                print("Admin notification error:", e)

        return


# =========================================================
# PAYMENT METHOD
# =========================================================

async def select_method(query, context, method):

    context.user_data["withdraw_method"] = method
    context.user_data["withdraw_step"] = "account"

    await query.edit_message_text(
        f"💳 **{method} Withdrawal**\n\n"
        "Please send your payment account number.\n\n"
        "Example:\n"
        "`01XXXXXXXXX`",
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(query):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Admin only!",
            show_alert=True
        )
        return

    cursor.execute(
        "SELECT COUNT(*) FROM users"
    )
    total_users = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'pending'
    """)
    pending = cursor.fetchone()[0]

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Pending Withdrawals",
                callback_data="admin_withdrawals"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Task",
                callback_data="admin_add_task"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Home",
                callback_data="home"
            )
        ]
    ]

    await query.edit_message_text(
        "👨‍💼 **Admin Panel**\n\n"
        f"👥 Total users: **{total_users}**\n"
        f"💸 Pending withdrawals: **{pending}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN STATISTICS
# =========================================================

async def admin_stats(query):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    cursor.execute(
        "SELECT COUNT(*) FROM users"
    )
    users = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COALESCE(SUM(points), 0) FROM users"
    )
    points = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM withdrawals"
    )
    withdrawals = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'pending'
    """)
    pending = cursor.fetchone()[0]

    await query.edit_message_text(
        "📊 **TaskMint Statistics**\n\n"
        f"👥 Users: **{users}**\n"
        f"💎 Total points: **{points}**\n"
        f"💸 Total withdrawals: **{withdrawals}**\n"
        f"⏳ Pending: **{pending}**",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin"
                )
            ]
        ]),
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def admin_withdrawals(query):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    cursor.execute("""
        SELECT id, user_id, amount, method, account
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY id DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()

    if not rows:

        await query.edit_message_text(
            "📋 **Pending Withdrawals**\n\n"
            "No pending withdrawals.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin"
                    )
                ]
            ]),
            parse_mode="Markdown"
        )
        return

    keyboard = []

    for withdrawal_id, user_id, amount, method, account in rows:

        keyboard.append([
            InlineKeyboardButton(
                f"#{withdrawal_id} • {amount} pts",
                callback_data=f"viewwd_{withdrawal_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin"
        )
    ])

    await query.edit_message_text(
        "📋 **Pending Withdrawals**\n\n"
        "Select a request:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# VIEW WITHDRAWAL
# =========================================================

async def view_withdrawal(query, withdrawal_id):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    cursor.execute("""
        SELECT id, user_id, username, amount, method, account, status
        FROM withdrawals
        WHERE id = ?
    """, (withdrawal_id,))

    row = cursor.fetchone()

    if not row:

        await query.answer(
            "Withdrawal not found!",
            show_alert=True
        )
        return

    wid, uid, username, amount, method, account, status = row

    keyboard = []

    if status == "pending":

        keyboard.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_{wid}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{wid}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin_withdrawals"
        )
    ])

    await query.edit_message_text(
        "💸 **Withdrawal Details**\n\n"
        f"🆔 Request: **#{wid}**\n"
        f"👤 User ID: `{uid}`\n"
        f"👤 Username: @{username or 'N/A'}\n"
        f"💰 Amount: **{amount} points**\n"
        f"💳 Method: **{method}**\n"
        f"📱 Account: `{account}`\n"
        f"📌 Status: **{status}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================================================
# APPROVE / REJECT
# =========================================================

async def approve_withdrawal(query, context, withdrawal_id):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    cursor.execute("""
        SELECT user_id, amount, status
        FROM withdrawals
        WHERE id = ?
    """, (withdrawal_id,))

    row = cursor.fetchone()

    if not row:
        await query.answer(
            "Request not found!",
            show_alert=True
        )
        return

    user_id, amount, status = row

    if status != "pending":
        await query.answer(
            "Already processed!",
            show_alert=True
        )
        return

    cursor.execute("""
        UPDATE withdrawals
        SET status = 'approved'
        WHERE id = ?
    """, (withdrawal_id,))

    db.commit()

    try:

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ **Withdrawal Approved!**\n\n"
                f"🆔 Request: #{withdrawal_id}\n"
                f"💰 Amount: {amount} points\n\n"
                "Your withdrawal has been approved by admin."
            ),
            parse_mode="Markdown"
        )

    except Exception as e:
        print("User notification error:", e)

    await query.answer(
        "Withdrawal approved!",
        show_alert=True
    )

    await admin_withdrawals(query)


async def reject_withdrawal(query, context, withdrawal_id):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    cursor.execute("""
        SELECT user_id, amount, status
        FROM withdrawals
        WHERE id = ?
    """, (withdrawal_id,))

    row = cursor.fetchone()

    if not row:
        await query.answer(
            "Request not found!",
            show_alert=True
        )
        return

    user_id, amount, status = row

    if status != "pending":
        await query.answer(
            "Already processed!",
            show_alert=True
        )
        return

    # Refund points
    cursor.execute("""
        UPDATE users
        SET points = points + ?
        WHERE user_id = ?
    """, (amount, user_id))

    cursor.execute("""
        UPDATE withdrawals
        SET status = 'rejected'
        WHERE id = ?
    """, (withdrawal_id,))

    db.commit()

    try:

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ **Withdrawal Rejected**\n\n"
                f"🆔 Request: #{withdrawal_id}\n"
                f"💰 Refunded: {amount} points\n\n"
                "The points have been returned to your balance."
            ),
            parse_mode="Markdown"
        )

    except Exception as e:
        print("User notification error:", e)

    await query.answer(
        "Withdrawal rejected and refunded!",
        show_alert=True
    )

    await admin_withdrawals(query)


# =========================================================
# ADD TASK
# =========================================================

async def admin_add_task(query, context):

    if not ADMIN_ID or query.from_user.id != ADMIN_ID:
        return

    context.user_data["admin_step"] = "task_title"

    await query.edit_message_text(
        "➕ **Add New Task**\n\n"
        "Send the task title.\n\n"
        "Example:\n"
        "`Join Telegram Channel`",
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN TEXT INPUT
# =========================================================

async def handle_admin_text(update, context):

    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return False

    text = update.message.text.strip()

    # Task title
    if context.user_data.get("admin_step") == "task_title":

        context.user_data["task_title"] = text
        context.user_data["admin_step"] = "task_reward"

        await update.message.reply_text(
            "💰 Send the reward points.\n\n"
            "Example: `20`"
        )

        return True

    # Task reward
    if context.user_data.get("admin_step") == "task_reward":

        try:
            reward = int(text)
        except:

            await update.message.reply_text(
                "❌ Enter a valid number."
            )
            return True

        context.user_data["task_reward"] = reward
        context.user_data["admin_step"] = "task_link"

        await update.message.reply_text(
            "🔗 Send the task link.\n\n"
            "Example:\n"
            "`https://t.me/example`"
        )

        return True

    # Task link
    if context.user_data.get("admin_step") == "task_link":

        title = context.user_data.get("task_title")
        reward = context.user_data.get("task_reward")
        link = text

        cursor.execute("""
            INSERT INTO tasks
            (title, reward, link)
            VALUES (?, ?, ?)
        """, (
            title,
            reward,
            link
        ))

        db.commit()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ **Task Added Successfully!**\n\n"
            f"🎯 {title}\n"
            f"💰 Reward: {reward} points\n"
            f"🔗 {link}",
            parse_mode="Markdown"
        )

        return True

    return False


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data

    # Home
    if data == "home":
        await main_menu(query)

    # Tasks
    elif data == "tasks":
        await show_tasks(query)

    # Open task
    elif data.startswith("task_"):

        task_id = int(data.split("_")[1])

        await open_task(
            query,
            task_id
        )

    # Complete task
    elif data.startswith("complete_"):

        task_id = int(data.split("_")[1])

        await complete_task(
            query,
            task_id
        )

    # Referral
    elif data == "refer":
        await show_referral(query)

    # Daily
    elif data == "daily":
        await daily_bonus(query)

    # Balance
    elif data == "balance":
        await show_balance(query)

    # Withdrawal
    elif data == "withdraw":
        await withdrawal_menu(query)

    elif data == "withdraw_request":
        await withdrawal_request(
            query,
            context
        )

    # Payment methods
    elif data == "method_bkash":
        await select_method(
            query,
            context,
            "bKash"
        )

    elif data == "method_nagad":
        await select_method(
            query,
            context,
            "Nagad"
        )

    # Admin
    elif data == "admin":
        await admin_panel(query)

    elif data == "admin_stats":
        await admin_stats(query)

    elif data == "admin_withdrawals":
        await admin_withdrawals(query)

    elif data == "admin_add_task":
        await admin_add_task(
            query,
            context
        )

    # View withdrawal
    elif data.startswith("viewwd_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        await view_withdrawal(
            query,
            withdrawal_id
        )

    # Approve
    elif data.startswith("approve_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        await approve_withdrawal(
            query,
            context,
            withdrawal_id
        )

    # Reject
    elif data.startswith("reject_"):

        withdrawal_id = int(
            data.split("_")[1]
        )

        await reject_withdrawal(
            query,
            context,
            withdrawal_id
        )

# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):
    print("ERROR:", context.error)


# =========================================================
# MAIN
# =========================================================

def main():
    print("Starting TaskMint Bot...")

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            all_text
        )
    )

    app.add_error_handler(error_handler)

    print("TaskMint Bot is running!")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
