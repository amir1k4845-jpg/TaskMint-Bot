import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

ADMIN_ID = 7003609983

REQUIRED_CHANNEL = "@TaskMint_v1"
CHANNEL_LINK = "https://t.me/TaskMint_v1"

TOKEN_NAME = "POL"
DEFAULT_MIN_WITHDRAW = 1.0
DEFAULT_REF_COMMISSION = 0.5
DATABASE_NAME = "taskmint"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is missing.")


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server started on port {port}")
    server.serve_forever()


# =========================================================
# MONGODB COLLECTIONS & SETTINGS
# =========================================================

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]
submissions_collection = db["task_submissions"]
settings_collection = db["settings"]


def setup_database():
    users_collection.create_index("user_id", unique=True)
    tasks_collection.create_index("task_id", unique=True)
    withdrawals_collection.create_index("user_id")
    withdrawals_collection.create_index("status")
    submissions_collection.create_index("user_id")
    submissions_collection.create_index("task_id")
    settings_collection.create_index("key", unique=True)
    print("MongoDB database ready.")


def get_setting(key, default_value):
    setting = settings_collection.find_one({"key": key})
    if setting:
        return setting.get("value", default_value)
    return default_value


def update_setting(key, value):
    settings_collection.update_one(
        {"key": key},
        {"$set": {"value": value}},
        upsert=True
    )
    
# =========================================================
# USER FUNCTIONS, STRICT CHANNEL CHECK & TASK COMPLETION
# =========================================================

def create_or_update_user(user, referrer_id=None):
    now = datetime.now(timezone.utc)
    existing_user = users_collection.find_one({"user_id": user.id})

    if not existing_user:
        ref_by = None
        if referrer_id and referrer_id != user.id:
            ref_user = users_collection.find_one({"user_id": referrer_id})
            if ref_user:
                ref_by = referrer_id
                users_collection.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"referrals": 1}}
                )

        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                },
                "$setOnInsert": {
                    "user_id": user.id,
                    "balance": 0.0,
                    "referrals": 0,
                    "referred_by": ref_by,
                    "ref_bonus_paid": False,
                    "completed_tasks": [],
                    "is_banned": False,
                    "created_at": now
                }
            },
            upsert=True
        )
    else:
        users_collection.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "updated_at": now
                }
            }
        )


def get_user(user_id):
    return users_collection.find_one({"user_id": user_id})


def get_balance(user_id):
    user = get_user(user_id)
    if not user:
        return 0.0
    return float(user.get("balance", 0.0))


def check_and_pay_referral(user_id, context):
    user = get_user(user_id)
    if not user:
        return

    if user.get("ref_bonus_paid", False):
        return

    referred_by = user.get("referred_by")
    completed_tasks = user.get("completed_tasks", [])

    if referred_by and len(completed_tasks) >= 4:
        ref_commission = float(get_setting("ref_commission", DEFAULT_REF_COMMISSION))
        
        users_collection.update_one(
            {"user_id": referred_by},
            {"$inc": {"balance": ref_commission}}
        )

        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"ref_bonus_paid": True}}
        )

        try:
            context.bot.send_message(
                referred_by,
                (
                    "🎉 <b>Referral Bonus Unlocked!</b>\n\n"
                    f"Your referred user has completed 4 tasks.\n"
                    f"💰 You received <b>+{ref_commission} {TOKEN_NAME}</b> commission!"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass


def main_menu(user_id):
    buttons = [
        ["🎯 Tasks"],
        ["💰 Balance", "💳 Withdraw"],
        ["👥 Refer"],
    ]
    if user_id == ADMIN_ID:
        buttons.append(["👑 Admin Panel"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Done", callback_data="check_join")]
    ])


async def is_channel_member(bot, user_id):
    """শক্তিশালী চ্যানেল মেম্বারশিপ চেক"""
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        return False
    except Exception as error:
        print("Channel check error:", error)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referrer_id = None
    if context.args:
        try:
            referrer_id = int(context.args[0])
        except ValueError:
            pass

    create_or_update_user(user, referrer_id)

    db_user = get_user(user.id)
    if db_user and db_user.get("is_banned", False):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return

    context.user_data.clear()
    await update.message.reply_text("Checking...", reply_markup=ReplyKeyboardRemove())

    member = await is_channel_member(context.bot, user.id)
    if not member:
        await update.message.reply_text(
            (
                "👋 <b>Hi dear user!</b>\n\n"
                "Please join our official channel "
                "then the bot will become active for you.\n\n"
                "After joining, press <b>Done</b>."
            ),
            reply_markup=join_keyboard(),
            parse_mode="HTML"
        )
        return

    await send_main_menu(context.bot, user.id)


async def send_main_menu(bot, user_id):
    await bot.send_message(
        chat_id=user_id,
        text=("🎉 <b>Welcome to TaskMint!</b>\n\nChoose an option below."),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    member = await is_channel_member(context.bot, user_id)
    if not member:
        await query.answer("❌ Please join the channel first.", show_alert=True)
        return

    await query.answer("✅ Verified!")
    await query.edit_message_text("✅ <b>Done!</b>\n\nYour membership has been verified.", parse_mode="HTML")
    await send_main_menu(context.bot, user_id)


async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    await update.message.reply_text(
        (
            "💰 <b>Your Balance</b>\n\n"
            f"💎 Balance: <b>{balance:.6f} {TOKEN_NAME}</b>"
        ),
        parse_mode="HTML"
    )


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = list(tasks_collection.find({"active": True}).sort("created_at", -1))
    if not tasks:
        await update.message.reply_text("🎯 <b>Tasks</b>\n\nNo tasks are available right now.", parse_mode="HTML")
        return

    buttons = []
    for task in tasks:
        total_slots = task.get("total_slots", 0)
        completed_slots = task.get("completed_slots", 0)
        remaining = total_slots - completed_slots

        if total_slots > 0 and remaining <= 0:
            tasks_collection.update_one({"task_id": task["task_id"]}, {"$set": {"active": False}})
            continue

        # ইউজারদের জন্য স্লট হাইড রাখা হয়েছে
        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task.get('title', 'Task')}",
                callback_data=f"task_{task['task_id']}"
            )
        ])

    if not buttons:
        await update.message.reply_text("🎯 <b>Tasks</b>\n\nNo tasks are available right now.", parse_mode="HTML")
        return

    await update.message.reply_text(
        "🎯 <b>Available Tasks</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )


async def task_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    task_id = query.data.replace("task_", "", 1)

    task = tasks_collection.find_one({"task_id": task_id, "active": True})
    if not task:
        await query.answer("❌ Task not found or inactive.", show_alert=True)
        return

    keyboard = []
    link = task.get("link")
    if link:
        keyboard.append([InlineKeyboardButton("🔗 Open Task Link", url=link)])

    task_type = task.get("task_type", "auto")
    if task_type == "auto":
        keyboard.append([InlineKeyboardButton("✅ Auto Verify & Complete", callback_data=f"complete_{task_id}")])
    else:
        keyboard.append([InlineKeyboardButton("📤 Submit Proof (Manual)", callback_data=f"submitproof_{task_id}")])

    await query.answer()
    await query.edit_message_text(
        (
            f"🎯 <b>{task.get('title', 'Task')}</b>\n\n"
            f"📝 {task.get('description', '')}\n\n"
            f"🔹 Type: <b>{task_type.upper()}</b>\n"
            f"💰 Reward: <b>{float(task.get('reward', 0)):.6f} {TOKEN_NAME}</b>"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def complete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    task_id = query.data.replace("complete_", "", 1)

    task = tasks_collection.find_one({"task_id": task_id, "active": True})
    if not task:
        await query.answer("❌ Task is no longer available.", show_alert=True)
        return

    # ফেইক ভেরিফিকেশন আটকানোর জন্য স্ট্রং চেক
    task_link = task.get("link", "")
    if "t.me/" in task_link:
        try:
            channel_username = "@" + task_link.split("t.me/")[-1].strip("/")
            member = await context.bot.get_chat_member(channel_username, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                await query.answer("❌ You have not joined the target channel yet! Join first.", show_alert=True)
                return
        except Exception:
            member = await is_channel_member(context.bot, user_id)
            if not member:
                await query.answer("❌ You must join the channel first to complete this task!", show_alert=True)
                return

    already_done = users_collection.find_one({"user_id": user_id, "completed_tasks": task_id})
    if already_done:
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    reward = float(task.get("reward", 0))
    total_slots = task.get("total_slots", 0)
    completed_slots = task.get("completed_slots", 0)
    if total_slots > 0 and completed_slots >= total_slots:
        tasks_collection.update_one({"task_id": task_id}, {"$set": {"active": False}})
        await query.answer("❌ Task slots are finished.", show_alert=True)
        return

    users_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {"balance": reward},
            "$addToSet": {"completed_tasks": task_id}
        }
    )
    tasks_collection.update_one({"task_id": task_id}, {"$inc": {"completed_slots": 1}})

    check_and_pay_referral(user_id, context)

    await query.answer("✅ Task completed successfully!", show_alert=True)
    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward: <b>+{reward:.6f} {TOKEN_NAME}</b>\n\n"
            "The reward has been added to your balance."
        ),
        parse_mode="HTML"
    )


async def request_proof_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    task_id = query.data.replace("submitproof_", "", 1)

    task = tasks_collection.find_one({"task_id": task_id, "active": True})
    if not task:
        await query.answer("❌ Task not available.", show_alert=True)
        return

    already_done = users_collection.find_one({"user_id": user_id, "completed_tasks": task_id})
    if already_done:
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    pending_sub = submissions_collection.find_one({"user_id": user_id, "task_id": task_id, "status": "pending"})
    if pending_sub:
        await query.answer("❌ You already have a pending submission for this task.", show_alert=True)
        return

    context.user_data["submitting_task_id"] = task_id
    await query.answer()
    await query.edit_message_text(
        (
            "📤 <b>Submit Manual Task Proof</b>\n\n"
            f"Task: <b>{task.get('title')}</b>\n\n"
            "Please send your proof (Text, Username, or Screenshot link) in the next message:"
        ),
        parse_mode="HTML"
    )


async def refer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    referrals = user.get("referrals", 0) if user else 0
    ref_commission = float(get_setting("ref_commission", DEFAULT_REF_COMMISSION))
    bot_info = await context.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start={user_id}"

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{referrals}</b>\n"
            f"🎁 Commission: <b>{ref_commission} {TOKEN_NAME}</b> (After referred user completes 4 tasks)\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>"
        ),
        parse_mode="HTML"
    )


async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    min_withdraw = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))
    context.user_data.clear()

    if balance < min_withdraw:
        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: <b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\n"
                "❌ Insufficient balance."
            ),
            parse_mode="HTML"
        )
        return

    context.user_data["withdraw_step"] = "amount"
    await update.message.reply_text(
        (
            "💳 <b>POL Withdrawal</b>\n\n"
            f"💰 Available: <b>{balance:.6f} POL</b>\n"
            f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\n"
            "Enter withdrawal amount:"
        ),
        parse_mode="HTML"
    )


async def process_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    step = context.user_data.get("withdraw_step")
    min_withdraw = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))

    if step == "amount":
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("❌ Enter a valid amount.")
            return

        balance = get_balance(user_id)
        if amount < min_withdraw:
            await update.message.reply_text(f"❌ Minimum is {min_withdraw} POL.")
            return
        if amount > balance:
            await update.message.reply_text("❌ Insufficient balance.")
            return

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "wallet"
        await update.message.reply_text("👛 <b>POL Wallet</b>\n\nSend your POL wallet address.", parse_mode="HTML")
        return

    if step == "wallet":
        wallet = text
        amount = context.user_data.get("withdraw_amount")
        if not amount:
            context.user_data.clear()
            return

        result = users_collection.update_one(
            {"user_id": user_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}}
        )

        if result.modified_count != 1:
            context.user_data.clear()
            await update.message.reply_text("❌ Insufficient balance.")
            return

        now = datetime.now(timezone.utc)
        withdrawal = {
            "user_id": user_id,
            "amount": amount,
            "token": TOKEN_NAME,
            "wallet": wallet,
            "status": "pending",
            "created_at": now,
            "updated_at": now
        }

        try:
            result = withdrawals_collection.insert_one(withdrawal)
        except PyMongoError:
            users_collection.update_one({"user_id": user_id}, {"$inc": {"balance": amount}})
            context.user_data.clear()
            await update.message.reply_text("❌ Withdrawal failed.")
            return

        wid = str(result.inserted_id)
        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Withdrawal Submitted</b>\n\n"
                f"🆔 ID: <code>{wid}</code>\n"
                f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                f"👛 Wallet: <code>{wallet}</code>\n"
                "📌 Status: <b>Pending</b>"
            ),
            parse_mode="HTML"
                )
                            
# =========================================================
# ADMIN PANEL, SETTINGS & MANAGEMENT
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("🎯 Manage Tasks", callback_data="admin_tasks")
        ],
        [
            InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals"),
            InlineKeyboardButton("📥 Submissions", callback_data="admin_submissions")
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance")
        ],
        [
            InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
        ]
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        ("👑 <b>Admin Panel</b>\n\nSelect an option:"),
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Unauthorized.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_home":
        await query.edit_message_text(
            "👑 <b>Admin Panel</b>\n\nSelect an option:",
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )
        return

    if data == "admin_stats":
        total_users = users_collection.count_documents({})
        total_tasks = tasks_collection.count_documents({})
        pending_w = withdrawals_collection.count_documents({"status": "pending"})
        pending_s = submissions_collection.count_documents({"status": "pending"})

        await query.edit_message_text(
            (
                "📊 <b>Statistics</b>\n\n"
                f"👥 Total Users: <b>{total_users}</b>\n"
                f"🎯 Total Tasks: <b>{total_tasks}</b>\n"
                f"🕐 Pending Withdrawals: <b>{pending_w}</b>\n"
                f"📥 Pending Submissions: <b>{pending_s}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
            parse_mode="HTML"
        )
        return

    if data == "admin_tasks":
        active = tasks_collection.count_documents({"active": True})
        inactive = tasks_collection.count_documents({"active": False})
        await query.edit_message_text(
            (
                "🎯 <b>Manage Tasks & Slots</b>\n\n"
                f"🟢 Active: <b>{active}</b> | 🔴 Inactive: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Task (with Slots)", callback_data="task_add")],
                [InlineKeyboardButton("📋 View/Delete Tasks", callback_data="task_list_admin")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "task_add":
        context.user_data["admin_action"] = "task_add_step1"
        await query.edit_message_text("➕ <b>Add New Task</b>\n\nSend task title:", parse_mode="HTML")
        return

    if data == "task_list_admin":
        tasks = list(tasks_collection.find({}))
        if not tasks:
            await query.edit_message_text("No tasks found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")]]))
            return
        buttons = []
        for t in tasks:
            rem = t.get('total_slots', 0) - t.get('completed_slots', 0)
            # টাস্ক ডিলিট এবং অন/অফ করার বাটন[span_4](start_span)[span_4](end_span)
            buttons.append([
                InlineKeyboardButton(f"[{'ON' if t['active'] else 'OFF'}] {t['title']} (Rem: {rem})", callback_data=f"admin_deltask_{t['task_id']}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"admin_rmtask_{t['task_id']}")
            ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
        await query.edit_message_text("Manage Tasks (Click Delete to remove completely):", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin_deltask_"):
        tid = data.replace("admin_deltask_", "")
        t = tasks_collection.find_one({"task_id": tid})
        if t:
            new_status = not t.get("active", True)
            tasks_collection.update_one({"task_id": tid}, {"$set": {"active": new_status}})
        
        tasks = list(tasks_collection.find({}))
        buttons = []
        for t in tasks:
            rem = t.get('total_slots', 0) - t.get('completed_slots', 0)
            buttons.append([
                InlineKeyboardButton(f"[{'ON' if t['active'] else 'OFF'}] {t['title']} (Rem: {rem})", callback_data=f"admin_deltask_{t['task_id']}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"admin_rmtask_{t['task_id']}")
            ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
        await query.edit_message_text("Task status updated. Manage Tasks:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin_rmtask_"):
        tid = data.replace("admin_rmtask_", "")
        tasks_collection.delete_one({"task_id": tid})
        await query.answer("🗑 Task deleted permanently!", show_alert=True)
        
        tasks = list(tasks_collection.find({}))
        buttons = []
        if tasks:
            for t in tasks:
                rem = t.get('total_slots', 0) - t.get('completed_slots', 0)
                buttons.append([
                    InlineKeyboardButton(f"[{'ON' if t['active'] else 'OFF'}] {t['title']} (Rem: {rem})", callback_data=f"admin_deltask_{t['task_id']}"),
                    InlineKeyboardButton("🗑 Delete", callback_data=f"admin_rmtask_{t['task_id']}")
                ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
        await query.edit_message_text("Task deleted successfully. Manage Tasks:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "admin_submissions":
        pending_subs = list(submissions_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending_subs:
            await query.edit_message_text("📥 <b>Pending Submissions</b>\n\nNo pending submissions.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]), parse_mode="HTML")
            return
        buttons = []
        for sub in pending_subs:
            sid = str(sub["_id"])
            buttons.append([InlineKeyboardButton(f"User: {sub['user_id']}", callback_data=f"sub_manage_{sid}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("📥 <b>Pending Submissions</b>\n\nSelect a submission to review:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("sub_manage_"):
        sid = data.replace("sub_manage_", "")
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
        except:
            sub = None
        if not sub:
            await query.answer("❌ Submission not found.", show_alert=True)
            return

        task = tasks_collection.find_one({"task_id": sub["task_id"]})
        task_name = task["title"] if task else "Unknown"

        await query.edit_message_text(
            (
                f"📥 <b>Submission Details</b>\n\n"
                f"👤 User ID: <code>{sub['user_id']}</code>\n"
                f"🎯 Task: <b>{task_name}</b>\n"
                f"📄 Proof: <code>{sub['proof']}</code>\n"
                f"📌 Status: <b>{sub['status']}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"sub_approve_{sid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"sub_reject_{sid}")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_submissions")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("sub_approve_") or data.startswith("sub_reject_"):
        action_type, sid = data.split("_")[1], data.split("_")[2]
        new_status = "approved" if action_type == "approve" else "rejected"

        sub = submissions_collection.find_one({"_id": ObjectId(sid)})
        if sub and sub["status"] == "pending":
            submissions_collection.update_one({"_id": ObjectId(sid)}, {"$set": {"status": new_status}})

            task = tasks_collection.find_one({"task_id": sub["task_id"]})
            reward = float(task.get("reward", 0)) if task else 0

            if new_status == "approved":
                already_done = users_collection.find_one({"user_id": sub["user_id"], "completed_tasks": sub["task_id"]})
                if not already_done:
                    users_collection.update_one(
                        {"user_id": sub["user_id"]},
                        {
                            "$inc": {"balance": reward},
                            "$addToSet": {"completed_tasks": sub["task_id"]}
                        }
                    )
                    tasks_collection.update_one({"task_id": sub["task_id"]}, {"$inc": {"completed_slots": 1}})

                    check_and_pay_referral(sub["user_id"], context)

                try:
                    await context.bot.send_message(sub["user_id"], f"✅ Your manual task proof has been approved! Rewarded: +{reward} {TOKEN_NAME}")
                except:
                    pass
            else:
                try:
                    await context.bot.send_message(sub["user_id"], f"❌ Your task proof has been rejected.")
                except:
                    pass

            await query.edit_message_text(f"✅ Submission marked as {new_status}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_submissions")]]))
        else:
            await query.answer("Already processed.", show_alert=True)
        return

    if data == "admin_withdrawals":
        pending = list(withdrawals_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending:
            await query.edit_message_text("💳 <b>Withdrawals</b>\n\nNo pending withdrawals.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]), parse_mode="HTML")
            return
        buttons = []
        for item in pending:
            wid = str(item["_id"])
            amount = float(item.get("amount", 0))
            buttons.append([InlineKeyboardButton(f"💰 {amount:.4f} POL", callback_data=f"withdraw_manage_{wid}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("💳 <b>Pending Withdrawals</b>\n\nSelect to manage:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("withdraw_manage_"):
        wid = data.replace("withdraw_manage_", "")
        try:
            wd = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        except:
            wd = None
        if not wd:
            await query.answer("❌ Not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                f"💳 <b>Withdrawal Details</b>\n\n"
                f"🆔 ID: <code>{wid}</code>\n"
                f"👤 User: <code>{wd['user_id']}</code>\n"
                f"💰 Amount: <b>{wd['amount']} POL</b>\n"
                f"👛 Wallet: <code>{wd['wallet']}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{wid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{wid}")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_withdrawals")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("wd_approve_") or data.startswith("wd_reject_"):
        action_type, wid = data.split("_")[1], data.split("_")[2]
        new_status = "approved" if action_type == "approve" else "rejected"
        wd = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        if wd and wd["status"] == "pending":
            withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": new_status}})
            if new_status == "rejected":
                users_collection.update_one({"user_id": wd["user_id"]}, {"$inc": {"balance": wd["amount"]}})
                try:
                    await context.bot.send_message(wd["user_id"], f"❌ Withdrawal rejected & refunded.")
                except:
                    pass
            else:
                try:
                    await context.bot.send_message(wd["user_id"], f"✅ Withdrawal approved!")
                except:
                    pass
            await query.edit_message_text(f"✅ Marked as {new_status}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_withdrawals")]]))
        return

    if data == "admin_users":
        total = users_collection.count_documents({})
        await query.edit_message_text(
            (f"👥 <b>User Management</b>\n\nTotal Users: <b>{total}</b>"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 Ban User", callback_data="user_ban")],
                [InlineKeyboardButton("🔓 Unban User", callback_data="user_unban")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "user_ban":
        context.user_data["admin_action"] = "user_ban_action"
        await query.edit_message_text("🚫 Send User ID to ban:", parse_mode="HTML")
        return
    if data == "user_unban":
        context.user_data["admin_action"] = "user_unban_action"
        await query.edit_message_text("🔓 Send User ID to unban:", parse_mode="HTML")
        return

    if data == "admin_balance":
        await query.edit_message_text(
            "💰 <b>Balance Management</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add POL", callback_data="balance_add"), InlineKeyboardButton("➖ Remove POL", callback_data="balance_remove")],
                [InlineKeyboardButton("🔎 Check Balance", callback_data="balance_check")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "admin_settings":
        cur_min_wd = get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW)
        cur_ref_comm = get_setting("ref_commission", DEFAULT_REF_COMMISSION)
        await query.edit_message_text(
            (
                "⚙️ <b>Bot Settings</b>\n\n"
                f"📌 Min Withdraw: <b>{cur_min_wd} POL</b>\n"
                f"🎁 Refer Commission: <b>{cur_ref_comm} POL</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Change Min Withdraw", callback_data="set_min_withdraw")],
                [InlineKeyboardButton("✏️ Change Refer Commission", callback_data="set_ref_commission")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "set_min_withdraw":
        context.user_data["admin_action"] = "set_min_withdraw_action"
        await query.edit_message_text("📌 Send new Minimum Withdraw amount:", parse_mode="HTML")
        return

    if data == "set_ref_commission":
        context.user_data["admin_action"] = "set_ref_commission_action"
        await query.edit_message_text("🎁 Send new Referral Commission amount:", parse_mode="HTML")
        return

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.edit_message_text("📢 <b>Broadcast</b>\n\nSend message to broadcast (or /cancel):", parse_mode="HTML")
        return


async def balance_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Unauthorized.", show_alert=True)
        return
    data = query.data
    await query.answer()

    if data == "balance_add":
        context.user_data["admin_action"] = "balance_add"
        await query.edit_message_text("➕ <b>Add POL</b>\n\nSend:\n<code>USER_ID AMOUNT</code>", parse_mode="HTML")
        return
    if data == "balance_remove":
        context.user_data["admin_action"] = "balance_remove"
        await query.edit_message_text("➖ <b>Remove POL</b>\n\nSend:\n<code>USER_ID AMOUNT</code>", parse_mode="HTML")
        return
    if data == "balance_check":
        context.user_data["admin_action"] = "balance_check"
        await query.edit_message_text("🔎 <b>Check Balance</b>\n\nSend User ID.", parse_mode="HTML")
        return
        
# =========================================================
# HANDLERS, ROUTERS & MAIN EXECUTION
# =========================================================

async def admin_text_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return False

    action = context.user_data.get("admin_action")
    text = update.message.text.strip()

    if action == "set_min_withdraw_action":
        try:
            val = float(text)
            update_setting("min_withdraw", val)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Minimum withdraw updated to {val} POL")
        except:
            await update.message.reply_text("❌ Send a valid number.")
        return True

    if action == "set_ref_commission_action":
        try:
            val = float(text)
            update_setting("ref_commission", val)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Referral commission updated to {val} POL")
        except:
            await update.message.reply_text("❌ Send a valid number.")
        return True

    if action and action.startswith("task_add_"):
        if text == "/cancel":
            context.user_data.clear()
            await update.message.reply_text("❌ Cancelled.")
            return True

        if action == "task_add_step1":
            context.user_data["new_t_title"] = text
            context.user_data["admin_action"] = "task_add_step2"
            await update.message.reply_text("Send task description:")
            return True
        elif action == "task_add_step2":
            context.user_data["new_t_desc"] = text
            context.user_data["admin_action"] = "task_add_step3"
            await update.message.reply_text("Send task link (URL or Telegram Channel Link like https://t.me/channel):")
            return True
        elif action == "task_add_step3":
            context.user_data["new_t_link"] = text
            context.user_data["admin_action"] = "task_add_step4"
            # অটো বা ম্যানুয়াল সিলেক্ট করার বাটন[span_5](start_span)[span_5](end_span)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Auto Verify (Channel Join)", callback_data="t_type_auto")],
                [InlineKeyboardButton("Manual Proof Task", callback_data="t_type_manual")]
            ])
            await update.message.reply_text("Select task type (Auto Verify or Manual Proof):", reply_markup=keyboard)
            return True
        elif action == "task_add_step4":
            try:
                context.user_data["new_t_reward"] = float(text)
                context.user_data["admin_action"] = "task_add_step5"
                await update.message.reply_text("Send total slots limit (e.g. 100):")
            except:
                await update.message.reply_text("❌ Send valid number for reward.")
            return True
        elif action == "task_add_step5":
            try:
                slots = int(text)
                task_id = str(uuid.uuid4())[:8]

                tasks_collection.insert_one({
                    "task_id": task_id,
                    "title": context.user_data["new_t_title"],
                    "description": context.user_data["new_t_desc"],
                    "link": context.user_data["new_t_link"],
                    "task_type": context.user_data.get("new_t_type", "auto"),
                    "reward": context.user_data["new_t_reward"],
                    "total_slots": slots,
                    "completed_slots": 0,
                    "active": True,
                    "created_at": datetime.now(timezone.utc)
                })

                context.user_data.clear()
                await update.message.reply_text("✅ Task added successfully with slots[span_6](start_span)[span_6](end_span)!")
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {e}")
            return True

    if action == "user_ban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} banned.")
        except:
            await update.message.reply_text("❌ Invalid ID.")
        return True

    if action == "user_unban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} unbanned.")
        except:
            await update.message.reply_text("❌ Invalid ID.")
        return True

    if action == "broadcast":
        if text == "/cancel":
            context.user_data.clear()
            await update.message.reply_text("❌ Cancelled.")
            return True
        users = users_collection.find({}, {"user_id": 1})
        sent = 0
        for user in users:
            try:
                await context.bot.send_message(chat_id=user["user_id"], text=text)
                sent += 1
            except:
                pass
        context.user_data.clear()
        await update.message.reply_text(f"📢 Broadcast sent to {sent} users.")
        return True

    if action == "balance_add":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Added {amount} to user {target_id}")
        except:
            await update.message.reply_text("❌ Format: USER_ID AMOUNT")
        return True

    if action == "balance_remove":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Removed {amount} from user {target_id}")
        except:
            await update.message.reply_text("❌ Invalid format or balance.")
        return True

    if action == "balance_check":
        try:
            target_id = int(text)
            bal = get_balance(target_id)
            context.user_data.clear()
            await update.message.reply_text(f"💰 Balance: <b>{bal} POL</b>", parse_mode="HTML")
        except:
            await update.message.reply_text("❌ Invalid User ID.")
        return True

    return False


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if db_user and db_user.get("is_banned", False):
        await update.message.reply_text("❌ You are banned.")
        return

    text = update.message.text
    member = await is_channel_member(context.bot, user_id)

    if not member:
        await update.message.reply_text("🔐 Please join the channel first.", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 Join and press Done:", reply_markup=join_keyboard())
        return

    if user_id == ADMIN_ID:
        handled = await admin_text_action(update, context)
        if handled:
            return

    if text == "🎯 Tasks":
        await tasks_menu(update, context)
    elif text == "💰 Balance":
        await balance_menu(update, context)
    elif text == "💳 Withdraw":
        await withdraw_menu(update, context)
    elif text == "👥 Refer":
        await refer_menu(update, context)
    elif text == "👑 Admin Panel":
        await admin_panel(update, context)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    task_id_to_submit = context.user_data.get("submitting_task_id")
    if task_id_to_submit:
        proof_text = update.message.text.strip()
        context.user_data.pop("submitting_task_id", None)

        submissions_collection.insert_one({
            "user_id": user_id,
            "task_id": task_id_to_submit,
            "proof": proof_text,
            "status": "pending",
            "created_at": datetime.now(timezone.utc)
        })

        await update.message.reply_text("✅ Proof submitted successfully! Admin will review it soon.")
        return

    if context.user_data.get("admin_action") and user_id == ADMIN_ID:
        handled = await admin_text_action(update, context)
        if handled:
            return

    if context.user_data.get("withdraw_step"):
        await process_withdraw(update, context)
        return

    await menu_handler(update, context)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user_id = query.from_user.id

    if data == "check_join":
        await check_join(update, context)
        return
    
    # টাস্ক টাইপ সিলেক্ট করার হ্যান্ডলার[span_7](start_span)[span_7](end_span)
    if data == "t_type_auto" or data == "t_type_manual":
        if user_id == ADMIN_ID and context.user_data.get("admin_action") == "task_add_step3":
            context.user_data["new_t_type"] = "auto" if data == "t_type_auto" else "manual"
            context.user_data["admin_action"] = "task_add_step4"
            await query.edit_message_text("Send reward amount (e.g. 0.5):")
            return

    if data.startswith("task_") and not data.startswith("task_add"):
        await task_details(update, context)
        return
    if data.startswith("complete_"):
        await complete_task(update, context)
        return
    if data.startswith("submitproof_"):
        await request_proof_input(update, context)
        return
    if data.startswith("balance_"):
        await balance_action_callback(update, context)
        return

    if user_id == ADMIN_ID:
        if any(data.startswith(x) for x in ["admin_", "task_add", "task_list", "withdraw_", "wd_", "sub_", "user_", "balance_", "set_"]):
            await admin_callback(update, context)
            return

    try:
        await query.answer("❌ Invalid action or unauthorized.", show_alert=True)
    except Exception:
        pass


async def error_handler(update, context):
    print("Telegram error:", repr(context.error))


def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    print("Connecting to MongoDB...")
    mongo_client.admin.command("ping")
    print("MongoDB connected successfully.")

    setup_database()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)

    print("TaskMint Bot is running with all updated features...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
            
