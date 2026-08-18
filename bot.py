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
MIN_WITHDRAW = 100
DAILY_REWARD = 10

DB_FILE = "taskmint.db"

AMOUNT, METHOD, ACCOUNT = range(3)


# =========================
# DATABASE
# =========================

def db():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()

    c.execute("""
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        user_id INTEGER,
        task_id TEXT,
        PRIMARY KEY(user_id, task_id)
    )
    """)

    c.execute("""
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

    c.commit()
    c.close()


def register_user(user, referrer=None):
    c = db()

    old = c.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user.id,)
    ).fetchone()

    if not old:
        c.execute("""
        INSERT INTO users
        (user_id,username,first_name,referred_by)
        VALUES(?,?,?,?)
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            referrer
        ))

        if referrer and referrer != user.id:
            c.execute("""
            UPDATE users
            SET points=points+?,
                referral_rewarded=1
            WHERE user_id=?
            """, (
                REFERRAL_REWARD,
                referrer
            ))

    else:
        c.execute("""
        UPDATE users
        SET username=?, first_name=?
        WHERE user_id=?
        """, (
            user.username or "",
            user.first_name or "",
            user.id
        ))

    c.commit()
    c.close()


def points(uid):
    c = db()
    r = c.execute(
        "SELECT points FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()
    c.close()
    return r["points"] if r else 0


def add_points(uid, amount):
    c = db()
    c.execute(
        "UPDATE users SET points=points+? WHERE user_id=?",
        (amount, uid)
    )
    c.commit()
    c.close()


def remove_points(uid, amount):
    c = db()
    c.execute(
        "UPDATE users SET points=MAX(points-?,0) WHERE user_id=?",
        (amount, uid)
    )
    c.commit()
    c.close()


def task_done(uid, task):
    c = db()
    r = c.execute(
        "SELECT 1 FROM tasks WHERE user_id=? AND task_id=?",
        (uid, task)
    ).fetchone()
    c.close()
    return r is not None


def save_task(uid, task):
    c = db()
    c.execute(
        "INSERT OR IGNORE INTO tasks VALUES(?,?)",
        (uid, task)
    )
    c.commit()
    c.close()


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        pass


def web_server():
    HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    ).serve_forever()


# =========================
# MENU
# =========================

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
# START
# =========================

async def start(update, context):

    user = update.effective_user

    ref = None

    if context.args:
        try:
            ref = int(context.args[0])
        except:
            ref = None

    register_user(user, ref)

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
        "ℹ️ TaskMint Help\n\n"
        "/start - Bot শুরু\n"
        "/help - Help\n"
        "/admin - Admin Panel\n\n"
        "যেকোনো সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।"
    )


# =========================
# EARN TASK
# =========================

async def earn_tasks(update, context):

    keys = [
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
        "📢 Channel Join করো\n\n"
        f"🎁 Reward: +{TASK_REWARD} Points\n\n"
        "Join করার পর Check Task চাপো।",
        reply_markup=InlineKeyboardMarkup(keys)
    )
# =========================
# TASK CHECK
# =========================

async def task_callback(update, context):

    q = update.callback_query
    await q.answer()

    uid = q.from_user.id

    if q.data != "check_join":
        return

    try:

        member = await context.bot.get_chat_member(
            CHANNEL_USERNAME,
            uid
        )

        if member.status in (
            "member",
            "administrator",
            "creator"
        ):

            if task_done(uid, "join_channel"):

                await q.edit_message_text(
                    "⚠️ এই Task আগেই complete করেছো!\n\n"
                    f"💰 Points: {points(uid)}"
                )
                return

            add_points(uid, TASK_REWARD)
            save_task(uid, "join_channel")

            await q.edit_message_text(
                "🎉 Task Completed!\n\n"
                f"✅ +{TASK_REWARD} Points\n"
                f"💰 Total: {points(uid)}"
            )

        else:

            await q.edit_message_text(
                "❌ তুমি এখনো Channel Join করোনি।",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "📢 Join Channel",
                            url=CHANNEL_URL
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Check Again",
                            callback_data="check_join"
                        )
                    ]
                ])
            )

    except Exception as e:

        print("Task error:", e)

        await q.edit_message_text(
            "⚠️ Verification failed.\n\n"
            "Bot-কে Channel Admin করা হয়েছে কিনা দেখো।"
        )


# =========================
# DAILY BONUS
# =========================

async def daily_bonus(update, context):

    uid = update.effective_user.id

    c = db()

    row = c.execute(
        "SELECT last_bonus FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()

    from datetime import datetime

    today = datetime.utcnow().strftime("%Y-%m-%d")

    if row and row["last_bonus"] == today:

        c.close()

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "⚠️ আজকের Bonus তুমি already নিয়েছো।\n\n"
            "আগামীকাল আবার নিতে পারবে।"
        )

        return

    c.execute("""
        UPDATE users
        SET points=points+?,
            last_bonus=?
        WHERE user_id=?
    """, (
        DAILY_REWARD,
        today,
        uid
    ))

    c.commit()
    c.close()

    await update.message.reply_text(
        "🎉 Daily Bonus Received!\n\n"
        f"🎁 +{DAILY_REWARD} Points\n"
        f"💰 Total Points: {points(uid)}"
    )


# =========================
# REFERRAL
# =========================

async def referral(update, context):

    uid = update.effective_user.id

    c = db()

    total = c.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE referred_by=?
    """, (uid,)).fetchone()[0]

    c.close()

    bot_username = context.bot.username

    link = (
        f"https://t.me/{bot_username}"
        f"?start={uid}"
    )

    await update.message.reply_text(
        "👥 Refer & Earn\n\n"
        "বন্ধুদের invite করে Points earn করো!\n\n"
        f"🎁 প্রতি referral: +{REFERRAL_REWARD} Points\n"
        f"👤 Total Referrals: {total}\n\n"
        "🔗 তোমার Referral Link:\n"
        f"{link}\n\n"
        "বন্ধুকে এই link পাঠাও।"
    )


# =========================
# WITHDRAW START
# =========================

async def withdraw_start(update, context):

    uid = update.effective_user.id
    bal = points(uid)

    if bal < MIN_WITHDRAW:

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            f"💰 Your Points: {bal}\n"
            f"⚠️ Minimum: {MIN_WITHDRAW} Points\n\n"
            "আরও Points earn করো।"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "💳 Withdrawal\n\n"
        f"💰 Available: {bal} Points\n"
        f"⚠️ Minimum: {MIN_WITHDRAW}\n\n"
        "কত Points withdraw করবে?\n"
        "শুধু সংখ্যা পাঠাও।"
    )

    return AMOUNT


# =========================
# WITHDRAW AMOUNT
# =========================

async def withdraw_amount(update, context):

    uid = update.effective_user.id
    bal = points(uid)

    try:
        amount = int(update.message.text.strip())
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

    if amount > bal:

        await update.message.reply_text(
            f"❌ Balance-এর চেয়ে বেশি চাওয়া হয়েছে।\n\n"
            f"💰 Available: {bal}"
        )

        return AMOUNT

    context.user_data["amount"] = amount

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
            "❌ Valid payment method নির্বাচন করো।"
        )

        return METHOD

    context.user_data["method"] = method

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

    uid = update.effective_user.id
    username = update.effective_user.username or "No Username"

    amount = context.user_data["amount"]
    method = context.user_data["method"]

    if amount > points(uid):

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Balance পরিবর্তিত হয়েছে। আবার চেষ্টা করো।",
            reply_markup=MARKUP
        )

        return ConversationHandler.END

    remove_points(uid, amount)

    c = db()

    cur = c.execute("""
        INSERT INTO withdrawals
        (user_id,username,amount,method,account,status)
        VALUES(?,?,?,?,?,'pending')
    """, (
        uid,
        username,
        amount,
        method,
        account
    ))

    wid = cur.lastrowid

    c.commit()
    c.close()

    text = (
        "🔔 NEW WITHDRAWAL\n\n"
        f"🆔 Request: #{wid}\n"
        f"👤 User ID: {uid}\n"
        f"👤 Username: @{username}\n"
        f"💰 Amount: {amount} Points\n"
        f"💳 Method: {method}\n"
        f"📱 Account: {account}\n"
        f"💰 Remaining: {points(uid)}"
    )

    keys = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_{wid}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{wid}"
            )
        ]
    ])

    try:

        await context.bot.send_message(
            ADMIN_ID,
            text,
            reply_markup=keys
        )

    except Exception as e:

        print("Admin notify error:", e)

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

    keys = InlineKeyboardMarkup([
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
        "শুধু Admin-এর জন্য।",
        reply_markup=keys
    )


# =========================
# ADMIN CALLBACK
# =========================

async def admin_callback(update, context):

    q = update.callback_query

    if q.from_user.id != ADMIN_ID:

        await q.answer(
            "❌ Unauthorized",
            show_alert=True
        )

        return

    await q.answer()

    data = q.data

    if data == "admin_users":

        c = db()

        total = c.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        c.close()

        await q.edit_message_text(
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

    elif data == "admin_stats":

        c = db()

        users = c.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total_points = c.execute(
            "SELECT COALESCE(SUM(points),0) FROM users"
        ).fetchone()[0]

        pending = c.execute("""
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status='pending'
        """).fetchone()[0]

        c.close()

        await q.edit_message_text(
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

    elif data == "admin_withdrawals":

        c = db()

        rows = c.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

        c.close()

        if not rows:

            text = (
                "💳 Pending Withdrawals\n\n"
                "No pending withdrawals."
            )

        else:

            text = "💳 PENDING WITHDRAWALS\n\n"

            for r in rows:

                text += (
                    f"#{r['id']} - "
                    f"{r['amount']} Points - "
                    f"{r['method']}\n"
                )

        await q.edit_message_text(
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

        context.user_data["broadcast"] = True

        await q.edit_message_text(
            "📢 BROADCAST\n\n"
            "এখন যে message সবাইকে পাঠাতে চাও "
            "সেটা পাঠাও।"
        )

    elif data == "admin_home":

        keys = InlineKeyboardMarkup([
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

        await q.edit_message_text(
            "👑 ADMIN PANEL",
            reply_markup=keys
        )

    elif data.startswith("approve_"):

        wid = int(data.split("_")[1])

        c = db()

        row = c.execute(
            "SELECT * FROM withdrawals WHERE id=?",
            (wid,)
        ).fetchone()

        if not row:

            c.close()

            await q.edit_message_text(
                "❌ Withdrawal not found."
            )

            return

        if row["status"] != "pending":

            c.close()

            await q.answer(
                "Already processed.",
                show_alert=True
            )

            return

        c.execute("""
            UPDATE withdrawals
            SET status='approved'
            WHERE id=?
        """, (wid,))

        c.commit()
        c.close()

        await q.edit_message_text(
            q.message.text +
            "\n\n✅ STATUS: APPROVED"
        )

        try:

            await context.bot.send_message(
                row["user_id"],
                "✅ Withdrawal Approved!\n\n"
                f"💰 Amount: {row['amount']} Points\n"
                f"💳 Method: {row['method']}"
            )

        except Exception:
            pass

    elif data.startswith("reject_"):

        wid = int(data.split("_")[1])

        c = db()

        row = c.execute(
            "SELECT * FROM withdrawals WHERE id=?",
            (wid,)
        ).fetchone()

        if not row:

            c.close()

            await q.edit_message_text(
                "❌ Withdrawal not found."
            )

            return

        if row["status"] != "pending":

            c.close()

            await q.answer(
                "Already processed.",
                show_alert=True
            )

            return

        c.execute("""
            UPDATE withdrawals
            SET status='rejected'
            WHERE id=?
        """, (wid,))

        c.execute("""
            UPDATE users
            SET points=points+?
            WHERE user_id=?
        """, (
            row["amount"],
            row["user_id"]
        ))

        c.commit()
        c.close()

        await q.edit_message_text(
            q.message.text +
            "\n\n❌ STATUS: REJECTED\n"
            f"↩️ {row['amount']} Points returned."
        )

        try:

            await context.bot.send_message(
                row["user_id"],
                "❌ Withdrawal Rejected\n\n"
                f"↩️ {row['amount']} Points ফেরত দেওয়া হয়েছে।"
            )

        except Exception:
            pass
