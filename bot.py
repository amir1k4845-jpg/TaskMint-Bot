import os
import sqlite3
import logging
from datetime import datetime, date

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "PUT_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

CHANNEL_USERNAME = "@TaskMint_v1"
DB_NAME = "taskmint.db"

REFERRAL_REWARD = 20
DAILY_BONUS = 10
MIN_WITHDRAW = 100

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            referred_by INTEGER,
            referral_rewarded INTEGER DEFAULT 0,
            daily_bonus_date TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT,
            instruction TEXT,
            reward REAL DEFAULT 0,
            total_slots INTEGER DEFAULT 0,
            completed_slots INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof TEXT,
            status TEXT DEFAULT 'pending',
            reward REAL DEFAULT 0,
            submitted_at TEXT,
            reviewed_at TEXT,
            UNIQUE(task_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            address TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            reviewed_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# USER DATABASE
# =========================================================

def get_user(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    return result


def create_user(
    user_id,
    username,
    first_name,
    referred_by=None
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO users (
                user_id,
                username,
                first_name,
                balance,
                referred_by,
                created_at
            )
            VALUES (?, ?, ?, 0, ?, ?)
        """, (
            user_id,
            username,
            first_name,
            referred_by,
            datetime.utcnow().isoformat()
        ))

    else:
        cur.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            user_id
        ))

    conn.commit()
    conn.close()


def add_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


# =========================================================
# TASK FUNCTIONS
# =========================================================

def get_task(task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,)
    )

    task = cur.fetchone()
    conn.close()

    return task


def task_has_submission(task_id, user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM task_submissions
        WHERE task_id = ? AND user_id = ?
    """, (
        task_id,
        user_id
    ))

    result = cur.fetchone()
    conn.close()

    return result


def available_slots(task):
    if task["total_slots"] <= 0:
        return 999999999

    return max(
        0,
        task["total_slots"] - task["completed_slots"]
    )


def automatically_disable_task(task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE tasks
        SET active = 0
        WHERE id = ?
        AND total_slots > 0
        AND completed_slots >= total_slots
    """, (task_id,))

    conn.commit()
    conn.close()


# =========================================================
# MENUS
# =========================================================

def user_menu():
    keyboard = [
        [
            "💰 Earn Tasks",
            "👥 Refer & Earn"
        ],
        [
            "💳 Withdraw",
            "🎁 Daily Bonus"
        ],
        [
            "📊 My Balance",
            "ℹ️ Help"
        ]
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


def admin_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Create Task",
                callback_data="admin_create"
            ),
            InlineKeyboardButton(
                "📋 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "📥 Pending Submissions",
                callback_data="admin_pending"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Withdrawals",
                callback_data="admin_withdrawals"
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================================================
# START
# =========================================================

async def start(update, context):

    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            ref = int(context.args[0])

            if ref != user.id:
                referred_by = ref

        except:
            pass

    old_user = get_user(user.id)

    create_user(
        user.id,
        user.username,
        user.first_name,
        referred_by
    )

    if (
        referred_by
        and not old_user
        and referred_by != user.id
    ):
        add_balance(
            referred_by,
            REFERRAL_REWARD
        )

        try:
            await context.bot.send_message(
                referred_by,
                f"🎉 New referral!\n\n"
                f"💰 +{REFERRAL_REWARD} reward added."
            )
        except:
            pass

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn rewards.\n"
        "👥 Invite friends and earn rewards.\n"
        "🎁 Claim your daily bonus.",
        reply_markup=user_menu()
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin_command(update, context):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Unauthorized."
        )
        return

    await update.message.reply_text(
        "🛠️ Admin Panel",
        reply_markup=admin_menu()
    )


init_db()
# =========================================================
# PART 2 / 5
# TASK CREATION SYSTEM
# =========================================================

async def create_task_start(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_action"] = "create_task"

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Channel Join",
                callback_data="type_channel"
            )
        ],
        [
            InlineKeyboardButton(
                "🐦 X Task",
                callback_data="type_x"
            ),
            InlineKeyboardButton(
                "📸 Instagram",
                callback_data="type_instagram"
            )
        ],
        [
            InlineKeyboardButton(
                "🤖 Bot Join",
                callback_data="type_bot"
            ),
            InlineKeyboardButton(
                "🔗 Custom",
                callback_data="type_custom"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="admin_panel"
            )
        ]
    ]

    await query.edit_message_text(
        "➕ Create New Task\n\n"
        "Select task type:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def select_task_type(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    task_type_map = {
        "type_channel": "channel",
        "type_x": "x",
        "type_instagram": "instagram",
        "type_bot": "bot",
        "type_custom": "custom",
    }

    task_type = task_type_map.get(
        query.data
    )

    if not task_type:
        return

    context.user_data["task_type"] = task_type
    context.user_data["create_step"] = "title"

    await query.edit_message_text(
        f"✅ Task Type: {task_type.upper()}\n\n"
        "Now send the Task Title.\n\n"
        "Example:\n"
        "Follow our X account"
    )


async def handle_task_creation(update, context):

    if not is_admin(update.effective_user.id):
        return False

    if context.user_data.get("admin_action") != "create_task":
        return False

    step = context.user_data.get("create_step")
    text = update.message.text.strip()

    # -----------------------------------------------------
    # TITLE
    # -----------------------------------------------------

    if step == "title":

        context.user_data["title"] = text
        context.user_data["create_step"] = "link"

        await update.message.reply_text(
            "🔗 Send the task link/username.\n\n"
            "Example:\n"
            "https://x.com/username\n\n"
            "For channel:\n"
            "https://t.me/TaskMint_v1"
        )

        return True

    # -----------------------------------------------------
    # LINK
    # -----------------------------------------------------

    if step == "link":

        context.user_data["link"] = text
        context.user_data["create_step"] = "reward"

        await update.message.reply_text(
            "💰 Send task reward.\n\n"
            "Example: 10"
        )

        return True

    # -----------------------------------------------------
    # REWARD
    # -----------------------------------------------------

    if step == "reward":

        try:
            reward = float(text)

            if reward <= 0:
                raise ValueError

        except:

            await update.message.reply_text(
                "❌ Invalid reward.\n"
                "Send a positive number."
            )

            return True

        context.user_data["reward"] = reward
        context.user_data["create_step"] = "slots"

        await update.message.reply_text(
            "🎯 Send total task slots.\n\n"
            "Example:\n"
            "100\n\n"
            "Send 0 for unlimited slots."
        )

        return True

    # -----------------------------------------------------
    # SLOTS
    # -----------------------------------------------------

    if step == "slots":

        try:
            slots = int(text)

            if slots < 0:
                raise ValueError

        except:

            await update.message.reply_text(
                "❌ Invalid slots.\n"
                "Send a whole number."
            )

            return True

        context.user_data["slots"] = slots
        context.user_data["create_step"] = "instruction"

        await update.message.reply_text(
            "📝 Send task instruction.\n\n"
            "Example:\n"
            "Follow the account and submit proof."
        )

        return True

    # -----------------------------------------------------
    # INSTRUCTION
    # -----------------------------------------------------

    if step == "instruction":

        context.user_data["instruction"] = text

        task_type = context.user_data["task_type"]
        title = context.user_data["title"]
        link = context.user_data["link"]
        reward = context.user_data["reward"]
        slots = context.user_data["slots"]

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO tasks (
                task_type,
                title,
                link,
                instruction,
                reward,
                total_slots,
                completed_slots,
                active,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
        """, (
            task_type,
            title,
            link,
            text,
            reward,
            slots,
            datetime.utcnow().isoformat()
        ))

        task_id = cur.lastrowid

        conn.commit()
        conn.close()

        context.user_data.clear()

        slot_text = (
            "Unlimited"
            if slots == 0
            else str(slots)
        )

        await update.message.reply_text(
            "✅ Task Created Successfully!\n\n"
            f"🆔 ID: {task_id}\n"
            f"📌 Type: {task_type.upper()}\n"
            f"📝 {title}\n"
            f"💰 Reward: {reward}\n"
            f"🎯 Slots: {slot_text}\n\n"
            "🟢 Status: ACTIVE",
            reply_markup=admin_menu()
        )

        return True

    return False


# =========================================================
# TASK LIST FOR USERS
# =========================================================

async def show_tasks(update, context):

    user_id = update.effective_user.id

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM tasks
        WHERE active = 1
        ORDER BY id DESC
    """)

    tasks = cur.fetchall()
    conn.close()

    if not tasks:

        await update.message.reply_text(
            "😔 No tasks available right now."
        )

        return

    buttons = []

    for task in tasks:

        if available_slots(task) <= 0:
            automatically_disable_task(
                task["id"]
            )
            continue

        if task_has_submission(
            task["id"],
            user_id
        ):
            continue

        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']} "
                f"(+{task['reward']})",
                callback_data=f"task_{task['id']}"
            )
        ])

    if not buttons:

        await update.message.reply_text(
            "😔 No new tasks available for you."
        )

        return

    await update.message.reply_text(
        "💰 Available Tasks:",
        reply_markup=InlineKeyboardMarkup(buttons)
        )
