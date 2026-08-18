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
            "❌ একটি valid Payment Method নির্বাচন করো।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
        "📱 এখন তোমার Payment Account পাঠাও।\n\n"
        "উদাহরণ:\n"
        "Binance UID / Email\n"
        "bKash Number\n"
        "Nagad Number"
    )

    return ACCOUNT
    if method not in (
        "💰 Binance",
        "📱 bKash",
        "📱 Nagad"
    ):

        await update.message.reply_text(
            "❌ একটি valid Payment Method নির্বাচন করো।"
        )

        return METHOD

    context.user_data[
        "withdraw_method"
    ] = method

    await update.message.reply_text(
        "📱 এখন তোমার Payment Account পাঠাও।\n\n"
        "উদাহরণ:\n"
        "Binance UID / Email\n"
        "bKash Number\n"
        "Nagad Number"
    )

    return ACCOUNT


# =========================
# WITHDRAW ACCOUNT
# =========================

async def withdraw_account(
    update,
    context
):

    user_id = update.effective_user.id

    account = (
        update.message.text.strip()
    )

    amount = context.user_data.get(
        "withdraw_amount"
    )

    method = context.user_data.get(
        "withdraw_method"
    )

    if not amount or not method:

        await update.message.reply_text(
            "❌ Withdraw session expired।\n\n"
            "আবার Withdraw থেকে শুরু করো।",
            reply_markup=MARKUP
        )

        return ConversationHandler.END

    if not account:

        await update.message.reply_text(
            "❌ Account information দিতে হবে।"
        )

        return ACCOUNT

    balance = points(user_id)

    if amount > balance:

        await update.message.reply_text(
            "❌ তোমার balance এখন আর যথেষ্ট নেই।\n\n"
            f"💰 Current Balance: {balance}",
            reply_markup=MARKUP
        )

        context.user_data.clear()

        return ConversationHandler.END

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT points, username
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

        if not row:

            conn.rollback()
            conn.close()

            await update.message.reply_text(
                "❌ User account পাওয়া যায়নি।",
                reply_markup=MARKUP
            )

            context.user_data.clear()

            return ConversationHandler.END

        current_points = row["points"]

        if current_points < amount:

            conn.rollback()
            conn.close()

            await update.message.reply_text(
                "❌ তোমার কাছে এত Points নেই!\n\n"
                f"💰 Available: {current_points}",
                reply_markup=MARKUP
            )

            context.user_data.clear()

            return ConversationHandler.END

        conn.execute(
            """
            UPDATE users
            SET points = points - ?
            WHERE user_id=?
              AND points >= ?
            """,
            (
                amount,
                user_id,
                amount
            )
        )

        if conn.total_changes != 1:

            conn.rollback()
            conn.close()

            await update.message.reply_text(
                "❌ Withdraw process করা যায়নি। আবার চেষ্টা করো।",
                reply_markup=MARKUP
            )

            context.user_data.clear()

            return ConversationHandler.END

        conn.execute(
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
                row["username"] or "",
                amount,
                method,
                account
            )
        )

        withdrawal_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

    except Exception as e:

        try:
            conn.rollback()
        except Exception:
            pass

        conn.close()

        print(
            "Withdrawal transaction error:",
            e
        )

        await update.message.reply_text(
            "❌ Withdraw process-এ সমস্যা হয়েছে।\n"
            "তোমার Points কাটা হয়নি।",
            reply_markup=MARKUP
        )

        context.user_data.clear()

        return ConversationHandler.END

    conn.close()

    new_balance = points(user_id)

    await update.message.reply_text(
        "✅ WITHDRAW REQUEST SUBMITTED!\n\n"
        f"🆔 Request ID: #{withdrawal_id}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n"
        f"📊 Remaining Balance: {new_balance}\n\n"
        "⏳ Admin verification-এর পর payment দেওয়া হবে।",
        reply_markup=MARKUP
    )

    if ADMIN_ID:

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 NEW WITHDRAWAL\n\n"
                    f"🆔 Request: #{withdrawal_id}\n"
                    f"👤 User ID: {user_id}\n"
                    f"👤 Username: "
                    f"@{update.effective_user.username}"
                    if update.effective_user.username
                    else f"👤 User ID: {user_id}\n"
                    f"💰 Amount: {amount} Points\n"
                    f"💳 Method: {method}\n"
                    f"📱 Account: {account}"
                )
            )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    context.user_data.clear()

    return ConversationHandler.END


# =========================
# WITHDRAW CANCEL
# =========================

async def withdraw_cancel(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Withdraw cancelled।",
        reply_markup=MARKUP
    )

    return ConversationHandler.END


# =========================
# MY BALANCE
# =========================

async def my_balance(
    update,
    context
):

    user_id = update.effective_user.id

    info = get_user_info(
        user_id
    )

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
        f"💰 Points: {user['points']}\n"
        f"👥 Referrals: {info['referrals']}\n"
        f"✅ Completed Tasks: {info['tasks']}\n\n"
        f"💳 Minimum Withdraw: {MIN_WITHDRAW} Points"
    )


# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id):

    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update,
    context
):

    user_id = update.effective_user.id

    if not is_admin(user_id):

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
                "👥 Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Points",
                callback_data="admin_add_points"
            ),
            InlineKeyboardButton(
                "➖ Remove Points",
                callback_data="admin_remove_points"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Task",
                callback_data="admin_add_task"
            ),
            InlineKeyboardButton(
                "🗑 Delete Task",
                callback_data="admin_delete_task"
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
        "👑 TASKMINT ADMIN PANEL\n\n"
        "নিচের option নির্বাচন করো:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(
    update,
    context
):

    query = update.callback_query

    user_id = query.from_user.id

    if not is_admin(user_id):

        await query.answer(
            "❌ Unauthorized",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    if data == "admin_stats":

        conn = db()

        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total_points = conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM users"
        ).fetchone()[0]

        tasks = conn.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0]

        withdrawals = conn.execute(
            "SELECT COUNT(*) FROM withdrawals"
        ).fetchone()[0]

        pending = conn.execute(
            """
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status='pending'
            """
        ).fetchone()[0]

        conn.close()

        await query.edit_message_text(
            "📊 TASKMINT STATISTICS\n\n"
            f"👥 Total Users: {users}\n"
            f"💰 Total User Points: {total_points}\n"
            f"✅ Completed Tasks: {tasks}\n"
            f"💳 Total Withdrawals: {withdrawals}\n"
            f"⏳ Pending Withdrawals: {pending}"
        )

        return

    if data == "admin_users":

        conn = db()

        rows = conn.execute(
            """
            SELECT user_id, username, points
            FROM users
            ORDER BY rowid DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

        if not rows:

            await query.edit_message_text(
                "👥 কোনো user নেই।"
            )

            return

        text = "👥 RECENT USERS\n\n"

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

        await query.edit_message_text(
            text
        )

        return

    if data == "admin_withdrawals":

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM withdrawals
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

        if not rows:

            await query.edit_message_text(
                "💳 কোনো withdrawal নেই।"
            )

            return

        text = "💳 RECENT WITHDRAWALS\n\n"

        for row in rows:

            text += (
                f"🆔 #{row['id']}\n"
                f"👤 User: {row['user_id']}\n"
                f"💰 Amount: {row['amount']}\n"
                f"💳 {row['method']}\n"
                f"📱 {row['account']}\n"
                f"📌 Status: {row['status']}\n\n"
            )

        await query.edit_message_text(
            text
        )

        return

    if data == "admin_add_points":

        context.user_data[
            "admin_action"
        ] = "add_points"

        await query.message.reply_text(
            "➕ Add Points\n\n"
            "এই format-এ পাঠাও:\n"
            "USER_ID AMOUNT\n\n"
            "Example:\n"
            "123456789 50"
        )

        return

    if data == "admin_remove_points":

        context.user_data[
            "admin_action"
        ] = "remove_points"

        await query.message.reply_text(
            "➖ Remove Points\n\n"
            "এই format-এ পাঠাও:\n"
            "USER_ID AMOUNT\n\n"
            "Example:\n"
            "123456789 50"
        )

        return

    if data == "admin_add_task":

        context.user_data[
            "admin_action"
        ] = "add_task"

        await query.message.reply_text(
            "➕ ADD TASK\n\n"
            "এই format-এ পাঠাও:\n\n"
            "@channel | https://t.me/channel | Title | Reward\n\n"
            "Example:\n"
            "@mychannel | https://t.me/mychannel | 📢 Join Channel | 10"
        )

        return

    if data == "admin_delete_task":

        rows = get_channel_tasks()

        if not rows:

            await query.edit_message_text(
                "🗑 Delete করার মতো কোনো Task নেই।"
            )

            return

        text = "🗑 ACTIVE TASKS\n\n"

        buttons = []

        for row in rows:

            text += (
                f"#{row['id']} - "
                f"{row['title']} "
                f"(+{row['reward']})\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"🗑 Delete #{row['id']}",
                    callback_data=(
                        f"delete_task_{row['id']}"
                    )
                )
            ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    if data.startswith(
        "delete_task_"
    ):

        try:

            task_id = int(
                data.split("_")[2]
            )

        except (
            ValueError,
            IndexError
        ):

            await query.answer(
                "Invalid task",
                show_alert=True
            )

            return

        delete_channel_task(
            task_id
        )

        await query.edit_message_text(
            f"✅ Task #{task_id} deleted successfully."
        )

        return

    if data == "admin_broadcast":

        context.user_data[
            "admin_action"
        ] = "broadcast"

        await query.message.reply_text(
            "📢 BROADCAST\n\n"
            "যে message সব users-কে পাঠাতে চাও "
            "সেটা এখন পাঠাও।"
        )

        return


# =========================
# ADMIN TEXT ACTIONS
# =========================

async def process_admin_text(
    update,
    context
):

    user_id = update.effective_user.id

    if not is_admin(user_id):

        return False

    action = context.user_data.get(
        "admin_action"
    )

    if not action:

        return False

    text = update.message.text.strip()

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

            target_id = int(
                parts[0]
            )

            amount = int(
                parts[1]
            )

        except ValueError:

            await update.message.reply_text(
                "❌ USER_ID এবং AMOUNT সংখ্যা হতে হবে।"
            )

            return True

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount অবশ্যই 0-এর বেশি হতে হবে।"
            )

            return True

        if action == "add_points":

            success = admin_add_points(
                target_id,
                amount
            )

            action_text = "added"

        else:

            success = admin_remove_points(
                target_id,
                amount
            )

            action_text = "removed"

        if not success:

            await update.message.reply_text(
                "❌ User পাওয়া যায়নি।"
            )

            return True

        await update.message.reply_text(
            f"✅ {amount} Points {action_text}.\n"
            f"👤 User ID: {target_id}\n"
            f"💰 Current Balance: {points(target_id)}"
        )

        context.user_data.pop(
            "admin_action",
            None
        )

        return True


    if action == "add_task":

        parts = [
            x.strip()
            for x in text.split("|")
        ]

        if len(parts) != 4:

            await update.message.reply_text(
                "❌ Format ভুল।\n\n"
                "@channel | https://t.me/channel | Title | Reward"
            )

            return True

        channel = parts[0]
        channel_url = parts[1]
        title = parts[2]

        try:

            reward = int(
                parts[3]
            )

        except ValueError:

            await update.message.reply_text(
                "❌ Reward অবশ্যই সংখ্যা হতে হবে।"
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

        await update.message.reply_text(
            "✅ Task added successfully!\n\n"
            f"📢 Channel: {channel}\n"
            f"📝 Title: {title}\n"
            f"💰 Reward: {reward}"
        )

        context.user_data.pop(
            "admin_action",
            None
        )

        return True


    if action == "broadcast":

        conn = db()

        rows = conn.execute(
            "SELECT user_id FROM users"
        ).fetchall()

        conn.close()

        sent = 0
        failed = 0

        await update.message.reply_text(
            "📢 Broadcast শুরু হয়েছে..."
        )

        for row in rows:

            try:

                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=text
                )

                sent += 1

            except Exception as e:

                failed += 1

                print(
                    "Broadcast error:",
                    row["user_id"],
                    e
                )

        await update.message.reply_text(
            "📢 BROADCAST COMPLETE\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )

        context.user_data.pop(
            "admin_action",
            None
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

    if await process_admin_text(
        update,
        context
    ):

        return

    text = update.message.text

    if text == "💰 Earn Tasks":

        await earn_tasks(
            update,
            context
        )

        return

    if text == "👥 Refer & Earn":

        await referral(
            update,
            context
        )

        return

    if text == "🎁 Daily Bonus":

        await daily_bonus(
            update,
            context
        )

        return

    if text == "📊 My Balance":

        await my_balance(
            update,
            context
        )

        return

    if text == "ℹ️ Help":

        await help_cmd(
            update,
            context
        )

        return

    await update.message.reply_text(
        "❓ আমি এই command বুঝতে পারিনি।\n\n"
        "নিচের Menu ব্যবহার করো।",
        reply_markup=MARKUP
    )
