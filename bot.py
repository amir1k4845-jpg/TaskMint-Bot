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
# =========================================================
# PART 3 / 5
# USER TASK + CHANNEL AUTO VERIFY
# =========================================================

async def task_details(update, context):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    task = get_task(task_id)

    if not task:
        await query.edit_message_text(
            "❌ Task not found."
        )
        return

    if not task["active"]:
        await query.edit_message_text(
            "⛔ This task is no longer active."
        )
        return

    if available_slots(task) <= 0:

        automatically_disable_task(task_id)

        await query.edit_message_text(
            "⛔ No slots remaining."
        )

        return

    if task_has_submission(
        task_id,
        user_id
    ):

        await query.edit_message_text(
            "⚠️ You have already submitted this task."
        )

        return

    text = (
        f"🎯 {task['title']}\n\n"
        f"📌 Type: {task['task_type'].upper()}\n"
        f"💰 Reward: {task['reward']}\n"
    )

    if task["total_slots"] > 0:
        remaining = available_slots(task)

        text += (
            f"🎯 Slots: "
            f"{task['completed_slots']}/"
            f"{task['total_slots']}\n"
            f"🔥 Remaining: {remaining}\n"
        )

    text += (
        f"\n📝 Instructions:\n"
        f"{task['instruction']}\n"
    )

    if task["link"]:
        text += (
            f"\n🔗 Link:\n"
            f"{task['link']}"
        )

    buttons = []

    if task["link"]:
        buttons.append([
            InlineKeyboardButton(
                "🔗 Open Task",
                url=task["link"]
            )
        ])

    if task["task_type"] == "channel":

        buttons.append([
            InlineKeyboardButton(
                "✅ Verify Join",
                callback_data=f"verify_{task_id}"
            )
        ])

    else:

        buttons.append([
            InlineKeyboardButton(
                "📤 Submit for Verification",
                callback_data=f"submit_{task_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="back_tasks"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# CHANNEL AUTO VERIFICATION
# =========================================================

async def verify_channel_join(update, context):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    task = get_task(task_id)

    if not task or task["task_type"] != "channel":
        await query.edit_message_text(
            "❌ Invalid task."
        )
        return

    if not task["active"]:
        await query.edit_message_text(
            "⛔ Task is no longer active."
        )
        return

    if available_slots(task) <= 0:

        automatically_disable_task(task_id)

        await query.edit_message_text(
            "⛔ All slots are completed."
        )

        return

    if task_has_submission(
        task_id,
        user_id
    ):

        await query.edit_message_text(
            "⚠️ You already completed this task."
        )

        return

    # -----------------------------------------------------
    # TELEGRAM CHANNEL CHECK
    # -----------------------------------------------------

    try:

        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id
        )

        joined_statuses = {
            "member",
            "administrator",
            "creator"
        }

        if member.status not in joined_statuses:

            await query.edit_message_text(
                "❌ You have not joined the channel yet.\n\n"
                f"Join {CHANNEL_USERNAME} first, "
                "then press Verify again."
            )

            return

    except Exception as e:

        logger.error(
            "Channel verification error: %s",
            e
        )

        await query.edit_message_text(
            "⚠️ Unable to verify your membership.\n"
            "Please try again later."
        )

        return

    # -----------------------------------------------------
    # RE-CHECK SLOT + INSERT
    # -----------------------------------------------------

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM tasks
        WHERE id = ?
        AND active = 1
    """, (task_id,))

    fresh_task = cur.fetchone()

    if not fresh_task:
        conn.close()

        await query.edit_message_text(
            "⛔ Task is no longer available."
        )

        return

    if (
        fresh_task["total_slots"] > 0
        and fresh_task["completed_slots"]
        >= fresh_task["total_slots"]
    ):

        cur.execute("""
            UPDATE tasks
            SET active = 0
            WHERE id = ?
        """, (task_id,))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            "⛔ All slots have been completed."
        )

        return

    try:

        cur.execute("""
            INSERT INTO task_submissions (
                task_id,
                user_id,
                proof,
                status,
                reward,
                submitted_at,
                reviewed_at
            )
            VALUES (?, ?, ?, 'approved', ?, ?, ?)
        """, (
            task_id,
            user_id,
            "Telegram channel membership",
            fresh_task["reward"],
            datetime.utcnow().isoformat(),
            datetime.utcnow().isoformat()
        ))

        cur.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            fresh_task["reward"],
            user_id
        ))

        cur.execute("""
            UPDATE tasks
            SET completed_slots = completed_slots + 1
            WHERE id = ?
        """, (task_id,))

        cur.execute("""
            UPDATE tasks
            SET active = 0
            WHERE id = ?
            AND total_slots > 0
            AND completed_slots >= total_slots
        """, (task_id,))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.rollback()
        conn.close()

        await query.edit_message_text(
            "⚠️ You already completed this task."
        )

        return

    conn.close()

    await query.edit_message_text(
        "✅ Task Verified Successfully!\n\n"
        f"💰 +{fresh_task['reward']} added to your balance."
    )


# =========================================================
# MANUAL SUBMISSION
# =========================================================

async def submit_task(update, context):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    task = get_task(task_id)

    if not task:
        await query.edit_message_text(
            "❌ Task not found."
        )
        return

    if not task["active"]:
        await query.edit_message_text(
            "⛔ Task is inactive."
        )
        return

    if available_slots(task) <= 0:
        automatically_disable_task(task_id)

        await query.edit_message_text(
            "⛔ No slots remaining."
        )
        return

    if task_has_submission(
        task_id,
        user_id
    ):

        await query.edit_message_text(
            "⚠️ Already submitted."
        )
        return

    context.user_data["submission_task_id"] = task_id

    await query.edit_message_text(
        "📤 Submit Task Proof\n\n"
        "Send your proof in the next message.\n\n"
        "Example:\n"
        "Your X username / Instagram username /\n"
        "Screenshot link / other proof."
    )


# =========================================================
# RECEIVE MANUAL PROOF
# =========================================================

async def receive_proof(update, context):

    task_id = context.user_data.get(
        "submission_task_id"
    )

    if not task_id:
        return False

    user_id = update.effective_user.id
    proof = update.message.text.strip()

    task = get_task(task_id)

    if not task:
        context.user_data.pop(
            "submission_task_id",
            None
        )

        await update.message.reply_text(
            "❌ Task no longer exists."
        )

        return True

    if not task["active"]:

        context.user_data.pop(
            "submission_task_id",
            None
        )

        await update.message.reply_text(
            "⛔ Task is no longer active."
        )

        return True

    if available_slots(task) <= 0:

        automatically_disable_task(task_id)

        context.user_data.pop(
            "submission_task_id",
            None
        )

        await update.message.reply_text(
            "⛔ All slots are completed."
        )

        return True

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("""
            INSERT INTO task_submissions (
                task_id,
                user_id,
                proof,
                status,
                reward,
                submitted_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?)
        """, (
            task_id,
            user_id,
            proof,
            task["reward"],
            datetime.utcnow().isoformat()
        ))

        submission_id = cur.lastrowid

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        await update.message.reply_text(
            "⚠️ You already submitted this task."
        )

        context.user_data.pop(
            "submission_task_id",
            None
        )

        return True

    conn.close()

    context.user_data.pop(
        "submission_task_id",
        None
    )

    await update.message.reply_text(
        "✅ Submission received!\n\n"
        "⏳ Your task is waiting for admin verification.\n"
        "You will receive your reward after approval."
    )

    # Notify admin
    try:

        await context.bot.send_message(
            ADMIN_ID,
            f"📥 NEW TASK SUBMISSION\n\n"
            f"🆔 Submission: {submission_id}\n"
            f"🎯 Task: {task['title']}\n"
            f"👤 User ID: {user_id}\n"
            f"💰 Reward: {task['reward']}\n\n"
            f"📌 Proof:\n{proof}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Approve",
                        callback_data=f"approve_{submission_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"reject_{submission_id}"
                    )
                ]
            ])
        )

    except Exception as e:

        logger.error(
            "Admin notification error: %s",
            e
        )

    return True
# =========================================================
# PART 4 / 5
# ADMIN VERIFICATION + TASK MANAGEMENT
# =========================================================

async def approve_submission(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        submission_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.*,
            t.title,
            t.active,
            t.total_slots,
            t.completed_slots,
            t.reward AS task_reward
        FROM task_submissions s
        JOIN tasks t
        ON s.task_id = t.id
        WHERE s.id = ?
    """, (submission_id,))

    submission = cur.fetchone()

    if not submission:
        conn.close()

        await query.edit_message_text(
            "❌ Submission not found."
        )

        return

    if submission["status"] != "pending":

        conn.close()

        await query.edit_message_text(
            f"⚠️ Already processed.\n\n"
            f"Status: {submission['status']}"
        )

        return

    # -----------------------------------------------------
    # SLOT CHECK
    # -----------------------------------------------------

    if (
        submission["total_slots"] > 0
        and submission["completed_slots"]
        >= submission["total_slots"]
    ):

        cur.execute("""
            UPDATE tasks
            SET active = 0
            WHERE id = ?
        """, (submission["task_id"],))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            "⛔ Cannot approve.\n"
            "All task slots are already completed."
        )

        return

    reward = submission["task_reward"]

    # -----------------------------------------------------
    # APPROVE
    # -----------------------------------------------------

    cur.execute("""
        UPDATE task_submissions
        SET status = 'approved',
            reward = ?,
            reviewed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        reward,
        datetime.utcnow().isoformat(),
        submission_id
    ))

    if cur.rowcount != 1:

        conn.rollback()
        conn.close()

        await query.edit_message_text(
            "⚠️ Submission was already processed."
        )

        return

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        submission["user_id"]
    ))

    cur.execute("""
        UPDATE tasks
        SET completed_slots = completed_slots + 1
        WHERE id = ?
    """, (
        submission["task_id"],
    ))

    cur.execute("""
        UPDATE tasks
        SET active = 0
        WHERE id = ?
        AND total_slots > 0
        AND completed_slots >= total_slots
    """, (
        submission["task_id"],
    ))

    conn.commit()

    cur.execute("""
        SELECT completed_slots, total_slots
        FROM tasks
        WHERE id = ?
    """, (
        submission["task_id"],
    ))

    updated_task = cur.fetchone()

    conn.close()

    await query.edit_message_text(
        "✅ Submission APPROVED\n\n"
        f"🆔 Submission: {submission_id}\n"
        f"👤 User: {submission['user_id']}\n"
        f"💰 Reward: +{reward}\n"
        f"🎯 Task: {submission['title']}"
    )

    try:

        await context.bot.send_message(
            submission["user_id"],
            f"🎉 Task Approved!\n\n"
            f"🎯 {submission['title']}\n"
            f"💰 +{reward} added to your balance."
        )

    except:
        pass


async def reject_submission(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        submission_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.*,
            t.title
        FROM task_submissions s
        JOIN tasks t
        ON s.task_id = t.id
        WHERE s.id = ?
    """, (submission_id,))

    submission = cur.fetchone()

    if not submission:
        conn.close()

        await query.edit_message_text(
            "❌ Submission not found."
        )

        return

    if submission["status"] != "pending":

        conn.close()

        await query.edit_message_text(
            f"⚠️ Already processed.\n\n"
            f"Status: {submission['status']}"
        )

        return

    cur.execute("""
        UPDATE task_submissions
        SET status = 'rejected',
            reviewed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        datetime.utcnow().isoformat(),
        submission_id
    ))

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "❌ Submission REJECTED\n\n"
        f"🆔 Submission: {submission_id}\n"
        f"👤 User: {submission['user_id']}\n"
        f"🎯 Task: {submission['title']}"
    )

    try:

        await context.bot.send_message(
            submission["user_id"],
            f"❌ Task Rejected\n\n"
            f"🎯 {submission['title']}\n\n"
            "Your submitted proof was rejected by admin."
        )

    except:
        pass


# =========================================================
# PENDING SUBMISSIONS
# =========================================================

async def pending_submissions(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.id,
            s.user_id,
            s.proof,
            s.reward,
            s.submitted_at,
            t.title
        FROM task_submissions s
        JOIN tasks t
        ON s.task_id = t.id
        WHERE s.status = 'pending'
        ORDER BY s.id ASC
        LIMIT 20
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:

        await query.edit_message_text(
            "📥 Pending Submissions\n\n"
            "✅ No pending submissions.",
            reply_markup=admin_menu()
        )

        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"🆔 {row['id']} | "
                f"{row['title'][:25]}",
                callback_data=f"submission_{row['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="admin_panel"
        )
    ])

    await query.edit_message_text(
        "📥 Pending Submissions\n\n"
        "Select a submission:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# SUBMISSION DETAILS
# =========================================================

async def submission_details(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        submission_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.*,
            t.title
        FROM task_submissions s
        JOIN tasks t
        ON s.task_id = t.id
        WHERE s.id = ?
    """, (
        submission_id,
    ))

    row = cur.fetchone()
    conn.close()

    if not row:

        await query.edit_message_text(
            "❌ Submission not found."
        )

        return

    text = (
        "📥 Submission Details\n\n"
        f"🆔 ID: {row['id']}\n"
        f"👤 User ID: {row['user_id']}\n"
        f"🎯 Task: {row['title']}\n"
        f"💰 Reward: {row['reward']}\n"
        f"📌 Status: {row['status']}\n\n"
        f"📝 Proof:\n{row['proof']}"
    )

    buttons = []

    if row["status"] == "pending":

        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_{row['id']}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{row['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Pending",
            callback_data="admin_pending"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# MANAGE TASKS
# =========================================================

async def manage_tasks(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM tasks
        ORDER BY id DESC
        LIMIT 30
    """)

    tasks = cur.fetchall()
    conn.close()

    if not tasks:

        await query.edit_message_text(
            "📋 No tasks created yet.",
            reply_markup=admin_menu()
        )

        return

    buttons = []

    for task in tasks:

        status = "🟢" if task["active"] else "🔴"

        buttons.append([
            InlineKeyboardButton(
                f"{status} #{task['id']} "
                f"{task['title'][:22]}",
                callback_data=f"manage_{task['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ Create Task",
            callback_data="admin_create"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="admin_panel"
        )
    ])

    await query.edit_message_text(
        "📋 Manage Tasks",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# TASK ADMIN DETAILS
# =========================================================

async def manage_task_details(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    task = get_task(task_id)

    if not task:
        await query.edit_message_text(
            "❌ Task not found."
        )
        return

    slot_text = (
        "Unlimited"
        if task["total_slots"] == 0
        else f"{task['completed_slots']}/"
             f"{task['total_slots']}"
    )

    status = (
        "🟢 ACTIVE"
        if task["active"]
        else "🔴 INACTIVE"
    )

    text = (
        f"🎯 Task #{task['id']}\n\n"
        f"📌 Type: {task['task_type'].upper()}\n"
        f"📝 {task['title']}\n"
        f"🔗 {task['link']}\n"
        f"💰 Reward: {task['reward']}\n"
        f"🎯 Slots: {slot_text}\n"
        f"📊 Status: {status}\n\n"
        f"📝 {task['instruction']}"
    )

    toggle_text = (
        "🔴 Deactivate"
        if task["active"]
        else "🟢 Activate"
    )

    buttons = [
        [
            InlineKeyboardButton(
                toggle_text,
                callback_data=f"toggle_{task_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ Delete",
                callback_data=f"delete_{task_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="admin_tasks"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def toggle_task(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE tasks
        SET active = CASE
            WHEN active = 1 THEN 0
            ELSE 1
        END
        WHERE id = ?
    """, (
        task_id,
    ))

    conn.commit()
    conn.close()

    await manage_task_details(
        update,
        context
    )


async def delete_task(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        task_id = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM tasks WHERE id = ?",
        (task_id,)
    )

    cur.execute(
        "DELETE FROM task_submissions WHERE task_id = ?",
        (task_id,)
    )

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "🗑️ Task deleted successfully.",
        reply_markup=admin_menu()
    )
# =========================================================
# PART 5A
# BALANCE + REFERRAL + DAILY BONUS + WITHDRAW
# =========================================================

# =========================================================
# BALANCE
# =========================================================

async def show_balance(update, context):

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        create_user(
            user_id,
            update.effective_user.username,
            update.effective_user.first_name
        )
        user = get_user(user_id)

    await update.message.reply_text(
        "📊 Your Balance\n\n"
        f"💰 Balance: {user['balance']}"
    )


# =========================================================
# REFERRAL
# =========================================================

async def referral(update, context):

    user_id = update.effective_user.id

    bot_info = await context.bot.get_me()

    link = (
        f"https://t.me/{bot_info.username}"
        f"?start={user_id}"
    )

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ?",
        (user_id,)
    )

    count = cur.fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "👥 Refer & Earn\n\n"
        f"💰 Reward per referral: {REFERRAL_REWARD}\n"
        f"👥 Total referrals: {count}\n\n"
        f"🔗 Your referral link:\n{link}"
    )


# =========================================================
# DAILY BONUS
# =========================================================

async def daily_bonus(update, context):

    user_id = update.effective_user.id

    today = date.today().isoformat()

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT daily_bonus_date FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()

    if row and row["daily_bonus_date"] == today:

        conn.close()

        await update.message.reply_text(
            "⏳ You already claimed today's bonus."
        )

        return

    cur.execute("""
        UPDATE users
        SET balance = balance + ?,
            daily_bonus_date = ?
        WHERE user_id = ?
    """, (
        DAILY_BONUS,
        today,
        user_id
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🎁 Daily Bonus Claimed!\n\n"
        f"💰 +{DAILY_BONUS} added to your balance."
    )


# =========================================================
# WITHDRAW START
# =========================================================

async def withdraw_start(update, context):

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        return

    if user["balance"] < MIN_WITHDRAW:

        await update.message.reply_text(
            "❌ Insufficient balance.\n\n"
            f"Minimum withdrawal: {MIN_WITHDRAW}\n"
            f"Your balance: {user['balance']}"
        )

        return

    context.user_data["withdraw_step"] = "amount"

    await update.message.reply_text(
        "💳 Withdraw\n\n"
        f"Minimum: {MIN_WITHDRAW}\n"
        f"Available: {user['balance']}\n\n"
        "Send withdrawal amount."
    )


# =========================================================
# WITHDRAW HANDLER
# =========================================================

async def handle_withdraw(update, context):

    step = context.user_data.get(
        "withdraw_step"
    )

    if step == "amount":

        try:
            amount = float(
                update.message.text.strip()
            )

        except:

            await update.message.reply_text(
                "❌ Invalid amount."
            )

            return True

        user = get_user(
            update.effective_user.id
        )

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                f"❌ Minimum withdrawal is {MIN_WITHDRAW}."
            )

            return True

        if amount > user["balance"]:

            await update.message.reply_text(
                "❌ Insufficient balance."
            )

            return True

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "address"

        await update.message.reply_text(
            "📥 Send your withdrawal address."
        )

        return True

    if step == "address":

        address = update.message.text.strip()

        amount = context.user_data.get(
            "withdraw_amount"
        )

        user_id = update.effective_user.id

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            AND balance >= ?
        """, (
            amount,
            user_id,
            amount
        ))

        if cur.rowcount != 1:

            conn.rollback()
            conn.close()

            await update.message.reply_text(
                "❌ Withdrawal failed."
            )

            context.user_data.clear()

            return True

        cur.execute("""
            INSERT INTO withdrawals (
                user_id,
                amount,
                address,
                status,
                created_at
            )
            VALUES (?, ?, ?, 'pending', ?)
        """, (
            user_id,
            amount,
            address,
            datetime.utcnow().isoformat()
        ))

        withdrawal_id = cur.lastrowid

        conn.commit()
        conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Withdrawal request submitted.\n\n"
            f"🆔 ID: {withdrawal_id}\n"
            f"💰 Amount: {amount}\n"
            f"📌 Status: Pending"
        )

        try:

            await context.bot.send_message(
                ADMIN_ID,
                "💸 NEW WITHDRAWAL\n\n"
                f"🆔 ID: {withdrawal_id}\n"
                f"👤 User: {user_id}\n"
                f"💰 Amount: {amount}\n"
                f"📥 Address:\n{address}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Pay",
                            callback_data=f"pay_{withdrawal_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"rejectwd_{withdrawal_id}"
                        )
                    ]
                ])
            )

        except Exception as e:

            logger.error(
                "Withdrawal admin notification error: %s",
                e
            )

        return True

    return False


# =========================================================
# ADMIN USERS
# =========================================================

async def admin_users(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM users"
    )

    total = cur.fetchone()[0]

    cur.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM users"
    )

    balance = cur.fetchone()[0]

    conn.close()

    await query.edit_message_text(
        "👥 User Statistics\n\n"
        f"👤 Total Users: {total}\n"
        f"💰 Total Balance: {balance}",
        reply_markup=admin_menu()
    )


# =========================================================
# ADMIN STATISTICS
# =========================================================

async def admin_stats(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM users"
    )
    users = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM tasks"
    )
    tasks = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE active = 1
    """)

    active_tasks = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM task_submissions
        WHERE status = 'pending'
    """)

    pending = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM task_submissions
        WHERE status = 'approved'
    """)

    approved = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'pending'
    """)

    pending_withdrawals = cur.fetchone()[0]

    conn.close()

    await query.edit_message_text(
        "📊 Statistics\n\n"
        f"👥 Users: {users}\n"
        f"📋 Total Tasks: {tasks}\n"
        f"🟢 Active Tasks: {active_tasks}\n"
        f"📥 Pending Submissions: {pending}\n"
        f"✅ Approved Submissions: {approved}\n"
        f"💸 Pending Withdrawals: {pending_withdrawals}",
        reply_markup=admin_menu()
        )
# =========================================================
# PART 5B
# WITHDRAW ADMIN + BUTTON HANDLER + TEXT HANDLER + MAIN
# =========================================================

# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def admin_withdrawals(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 20
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:

        await query.edit_message_text(
            "💸 Pending Withdrawals\n\n"
            "✅ No pending withdrawals.",
            reply_markup=admin_menu()
        )

        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"#{row['id']} | {row['amount']}",
                callback_data=f"wd_{row['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="admin_panel"
        )
    ])

    await query.edit_message_text(
        "💸 Pending Withdrawals",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# WITHDRAWAL DETAILS
# =========================================================

async def withdrawal_details(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        wid = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (wid,)
    )

    row = cur.fetchone()
    conn.close()

    if not row:

        await query.edit_message_text(
            "❌ Withdrawal not found."
        )

        return

    await query.edit_message_text(
        "💸 Withdrawal\n\n"
        f"🆔 ID: {row['id']}\n"
        f"👤 User: {row['user_id']}\n"
        f"💰 Amount: {row['amount']}\n"
        f"📥 Address:\n{row['address']}\n"
        f"📌 Status: {row['status']}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Pay",
                    callback_data=f"pay_{row['id']}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"rejectwd_{row['id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="admin_withdrawals"
                )
            ]
        ])
    )


# =========================================================
# PAY WITHDRAWAL
# =========================================================

async def pay_withdrawal(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        wid = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (wid,)
    )

    row = cur.fetchone()

    if not row:

        conn.close()

        await query.edit_message_text(
            "❌ Withdrawal not found."
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.edit_message_text(
            "⚠️ Already processed."
        )

        return

    cur.execute("""
        UPDATE withdrawals
        SET status = 'paid',
            reviewed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        datetime.utcnow().isoformat(),
        wid
    ))

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "✅ Withdrawal marked as PAID.\n\n"
        f"🆔 ID: {wid}\n"
        f"💰 Amount: {row['amount']}"
    )

    try:

        await context.bot.send_message(
            row["user_id"],
            "✅ Withdrawal Paid!\n\n"
            f"💰 Amount: {row['amount']}"
        )

    except:
        pass


# =========================================================
# REJECT WITHDRAWAL
# =========================================================

async def reject_withdrawal(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        wid = int(
            query.data.split("_")[1]
        )
    except:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (wid,)
    )

    row = cur.fetchone()

    if not row:

        conn.close()

        await query.edit_message_text(
            "❌ Withdrawal not found."
        )

        return

    if row["status"] != "pending":

        conn.close()

        await query.edit_message_text(
            "⚠️ Already processed."
        )

        return

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        row["amount"],
        row["user_id"]
    ))

    cur.execute("""
        UPDATE withdrawals
        SET status = 'rejected',
            reviewed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        datetime.utcnow().isoformat(),
        wid
    ))

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "❌ Withdrawal rejected.\n\n"
        f"🆔 ID: {wid}\n"
        f"💰 {row['amount']} refunded."
    )

    try:

        await context.bot.send_message(
            row["user_id"],
            "❌ Withdrawal Rejected.\n\n"
            f"💰 {row['amount']} has been returned to your balance."
        )

    except:
        pass


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(update, context):

    query = update.callback_query
    data = query.data

    if data == "admin_panel":

        if not is_admin(query.from_user.id):

            await query.answer(
                "Unauthorized.",
                show_alert=True
            )

            return

        await query.answer()

        await query.edit_message_text(
            "🛠️ Admin Panel",
            reply_markup=admin_menu()
        )

        return

    if data == "admin_create":
        await create_task_start(update, context)
        return

    if data.startswith("type_"):
        await select_task_type(update, context)
        return

    if data == "admin_tasks":
        await manage_tasks(update, context)
        return

    if data.startswith("manage_"):
        await manage_task_details(update, context)
        return

    if data.startswith("toggle_"):
        await toggle_task(update, context)
        return

    if data.startswith("delete_"):
        await delete_task(update, context)
        return

    if data == "admin_pending":
        await pending_submissions(update, context)
        return

    if data.startswith("submission_"):
        await submission_details(update, context)
        return

    if data.startswith("approve_"):
        await approve_submission(update, context)
        return

    if data.startswith("reject_"):
        await reject_submission(update, context)
        return

    if data == "admin_users":
        await admin_users(update, context)
        return

    if data == "admin_stats":
        await admin_stats(update, context)
        return

    if data == "admin_withdrawals":
        await admin_withdrawals(update, context)
        return

    if data.startswith("wd_"):
        await withdrawal_details(update, context)
        return

    if data.startswith("pay_"):
        await pay_withdrawal(update, context)
        return

    if data.startswith("rejectwd_"):
        await reject_withdrawal(update, context)
        return

    if data.startswith("task_"):
        await task_details(update, context)
        return

    if data.startswith("verify_"):
        await verify_channel_join(update, context)
        return

    if data.startswith("submit_"):
        await submit_task(update, context)
        return

    if data == "back_tasks":

        await query.answer()

        user_id = query.from_user.id

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

            await query.edit_message_text(
                "😔 No tasks available."
            )

            return

        await query.edit_message_text(
            "💰 Available Tasks:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(update, context):

    text = update.message.text

    if is_admin(update.effective_user.id):

        if context.user_data.get(
            "admin_action"
        ) == "create_task":

            handled = await handle_task_creation(
                update,
                context
            )

            if handled:
                return

    if context.user_data.get(
        "submission_task_id"
    ):

        handled = await receive_proof(
            update,
            context
        )

        if handled:
            return

    if context.user_data.get(
        "withdraw_step"
    ):

        handled = await handle_withdraw(
            update,
            context
        )

        if handled:
            return

    if text == "💰 Earn Tasks":

        await show_tasks(
            update,
            context
        )

    elif text == "📊 My Balance":

        await show_balance(
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

    elif text == "💳 Withdraw":

        await withdraw_start(
            update,
            context
        )

    elif text == "ℹ️ Help":

        await update.message.reply_text(
            "ℹ️ TaskMint Help\n\n"
            "💰 Earn Tasks - Complete available tasks\n"
            "👥 Refer - Invite friends\n"
            "🎁 Daily Bonus - Claim once per day\n"
            "📊 Balance - Check your balance\n"
            "💳 Withdraw - Request payment"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):

    logger.error(
        "Telegram error: %s",
        context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if TOKEN == "PUT_BOT_TOKEN_HERE":

        print(
            "ERROR: Set BOT_TOKEN environment variable."
        )

        return

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "TaskMint Bot is starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
