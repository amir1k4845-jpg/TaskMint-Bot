import os
import threading
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse
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
    
mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

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
        
async def check_and_pay_referral(user_id, context):
    user = get_user(user_id)
    if not user or user.get("ref_bonus_paid", False):
        return

    referred_by = user.get("referred_by")
    completed_tasks = user.get("completed_tasks", [])

    if referred_by and len(completed_tasks) >= 4:
        ref_commission = float(get_setting("ref_commission", DEFAULT_REF_COMMISSION))

        result = users_collection.update_one(
            {
                "user_id": user_id,
                "ref_bonus_paid": False,
                "referred_by": {"$ne": None}
            },
            {"$set": {"ref_bonus_paid": True}}
        )

        if result.modified_count != 1:
            return

        users_collection.update_one(
            {"user_id": referred_by},
            {"$inc": {"balance": ref_commission}}
        )

        try:
            await context.bot.send_message(
                referred_by,
                (
                    "🎉 <b>Referral Bonus Unlocked!</b>\n\n"
                    "Your referred user has completed 4 tasks.\n"
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
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
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
        text="🎉 <b>Welcome to TaskMint!</b>\n\nChoose an option below.",
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
    await query.edit_message_text(
        "✅ <b>Done!</b>\n\nYour membership has been verified.",
        parse_mode="HTML"
    )
    await send_main_menu(context.bot, user_id)
async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    await update.message.reply_text(
        f"💰 <b>Your Balance</b>\n\n💎 Balance: <b>{balance:.6f} {TOKEN_NAME}</b>",
        parse_mode="HTML"
    )
    
async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = list(tasks_collection.find({"active": True}).sort("created_at", -1))

    if not tasks:
        await update.message.reply_text(
            "🎯 <b>Tasks</b>\n\nNo tasks are available right now.",
            parse_mode="HTML"
        )
        return

    buttons = []
    for task in tasks:
        total_slots = int(task.get("total_slots", 0) or 0)
        completed_slots = int(task.get("completed_slots", 0) or 0)
        remaining = total_slots - completed_slots

        if total_slots > 0 and remaining <= 0:
            tasks_collection.update_one({"task_id": task["task_id"]}, {"$set": {"active": False}})
            continue

        buttons.append([
            InlineKeyboardButton(
                f"🎯 {escape(str(task.get('title', 'Task')))}",
                callback_data=f"task_{task['task_id']}"
            )
        ])

    if not buttons:
        await update.message.reply_text(
            "🎯 <b>Tasks</b>\n\nNo tasks are available right now.",
            parse_mode="HTML"
        )
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
            f"🎯 <b>{escape(str(task.get('title', 'Task')))}</b>\n\n"
            f"📝 {escape(str(task.get('description', '')))}\n\n"
            f"🔹 Type: <b>{escape(str(task_type).upper())}</b>\n"
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

    task_link = str(task.get("link", "") or "").strip()
    if "t.me/" in task_link:
        try:
            parsed = urlparse(task_link)
            channel_path = parsed.path.strip("/").split("/")[0]
            channel_username = "@" + channel_path if channel_path else ""

            if not channel_username:
                raise ValueError("Invalid Telegram channel link")

            member = await context.bot.get_chat_member(channel_username, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                await query.answer("❌ You have not joined the target channel yet! Join first.", show_alert=True)
                return
        except Exception:
            await query.answer("❌ Could not verify this Telegram task. Please contact admin.", show_alert=True)
            return

    already_done = users_collection.find_one({"user_id": user_id, "completed_tasks": task_id})
    if already_done:
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    reward = float(task.get("reward", 0))
    total_slots = int(task.get("total_slots", 0) or 0)

    slot_filter = {"task_id": task_id, "active": True}
    if total_slots > 0:
        slot_filter["$expr"] = {"$lt": [{"$ifNull": ["$completed_slots", 0]}, "$total_slots"]}

    slot_result = tasks_collection.update_one(slot_filter, {"$inc": {"completed_slots": 1}})
    if slot_result.modified_count != 1:
        tasks_collection.update_one(
            {"task_id": task_id, "active": True, "total_slots": {"$gt": 0}},
            {"$set": {"active": False}}
        )
        await query.answer("❌ Task slots are finished.", show_alert=True)
        return

    user_result = users_collection.update_one(
        {"user_id": user_id, "completed_tasks": {"$ne": task_id}},
        {"$inc": {"balance": reward}, "$addToSet": {"completed_tasks": task_id}}
    )

    if user_result.modified_count != 1:
        tasks_collection.update_one({"task_id": task_id}, {"$inc": {"completed_slots": -1}})
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    if total_slots > 0:
        tasks_collection.update_one(
            {"task_id": task_id, "completed_slots": {"$gte": total_slots}},
            {"$set": {"active": False}}
        )

    await check_and_pay_referral(user_id, context)
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

    if users_collection.find_one({"user_id": user_id, "completed_tasks": task_id}):
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    context.user_data["submitting_task_id"] = task_id
    await query.answer()
    await query.edit_message_text(
        (
            "📤 <b>Submit Manual Task Proof</b>\n\n"
            f"Task: <b>{escape(str(task.get('title', 'Task')))}</b>\n\n"
            "Please send your proof (text, link, or screenshot)."
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
            f"🎁 Commission: <b>{ref_commission} {TOKEN_NAME}</b>\n\n"
            f"🔗 <b>Your Referral Link:</b>\n<code>{escape(referral_link)}</code>"
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
                f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\n❌ Insufficient balance."
            ),
            parse_mode="HTML"
        )
        return

    context.user_data["withdraw_step"] = "amount"
    await update.message.reply_text(
        (
            "💳 <b>POL Withdrawal</b>\n\n"
            f"💰 Available: <b>{balance:.6f} POL</b>\n"
            f"📌 Minimum: <b>{min_withdraw:.6f} POL</b>\n\nEnter withdrawal amount:"
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

        if amount <= 0:
            await update.message.reply_text("❌ Amount must be greater than 0.")
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

        if len(wallet) < 20:
            await update.message.reply_text("❌ Please send a valid wallet address.")
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
                f"👛 Wallet: <code>{escape(wallet)}</code>\n"
                "📌 Status: <b>Pending</b>"
            ),
            parse_mode="HTML"
        )
        
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), InlineKeyboardButton("🎯 Manage Tasks", callback_data="admin_tasks")],
        [InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals"), InlineKeyboardButton("📥 Submissions", callback_data="admin_submissions")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"), InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("👑 <b>Admin Panel</b>\n\nSelect an option:", reply_markup=admin_keyboard(), parse_mode="HTML")
                
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Unauthorized.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_home":
        await query.edit_message_text("👑 <b>Admin Panel</b>\n\nSelect an option:", reply_markup=admin_keyboard(), parse_mode="HTML")
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
                f"🟢 Active: <b>{active}</b>\n"
                f"🔴 Inactive: <b>{inactive}</b>"
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
        context.user_data["admin_action"] = "task_add_title"
        context.user_data["new_task"] = {}
        await query.edit_message_text("➕ <b>Add New Task</b>\n\nSend task title:", parse_mode="HTML")
        return

    if data == "task_list_admin":
        tasks = list(tasks_collection.find({}).sort("created_at", -1).limit(10))
        if not tasks:
            await query.edit_message_text(
                "📋 <b>Task List</b>\n\nNo tasks found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")]]),
                parse_mode="HTML"
            )
            return
        buttons = []
        for t in tasks:
            status = "🟢" if t.get("active") else "🔴"
            buttons.append([
                InlineKeyboardButton(f"{status} {t.get('title')}", callback_data=f"admintask_view_{t['task_id']}")
            ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
        await query.edit_message_text("📋 <b>Task List (Click to Delete/Manage)</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("admintask_view_"):
        tid = data.replace("admintask_view_", "", 1)
        t = tasks_collection.find_one({"task_id": tid})
        if not t:
            await query.answer("❌ Task not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                f"🎯 <b>Task: {escape(str(t.get('title')))}</b>\n\n"
                f"📝 Desc: {escape(str(t.get('description')))}\n"
                f"🔗 Link: {escape(str(t.get('link')))}\n"
                f"💰 Reward: {t.get('reward')} {TOKEN_NAME}\n"
                f"👥 Slots: {t.get('completed_slots', 0)}/{t.get('total_slots', 0)}"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Delete Task", callback_data=f"admintask_del_{tid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="task_list_admin")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("admintask_del_"):
        tid = data.replace("admintask_del_", "", 1)
        tasks_collection.delete_one({"task_id": tid})
        await query.answer("✅ Task deleted successfully!", show_alert=True)
        
        active = tasks_collection.count_documents({"active": True})
        inactive = tasks_collection.count_documents({"active": False})
        await query.edit_message_text(
            (
                "🎯 <b>Manage Tasks & Slots</b>\n\n"
                f"🟢 Active: <b>{active}</b>\n"
                f"🔴 Inactive: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Task (with Slots)", callback_data="task_add")],
                [InlineKeyboardButton("📋 View/Delete Tasks", callback_data="task_list_admin")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "admin_withdrawals":
        pending_w = list(withdrawals_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending_w:
            await query.edit_message_text(
                "💳 <b>Pending Withdrawals</b>\n\nNo pending withdrawals.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
                parse_mode="HTML"
            )
            return
        buttons = [[InlineKeyboardButton(f"User: {w['user_id']} - {w['amount']} POL", callback_data=f"wd_manage_{w['_id']}")] for w in pending_w]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("💳 <b>Pending Withdrawals</b>\n\nSelect to manage:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("wd_manage_"):
        wid = data.replace("wd_manage_", "", 1)
        try:
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        except Exception:
            w = None
        if not w:
            await query.answer("❌ Withdrawal not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                "💳 <b>Withdrawal Details</b>\n\n"
                f"👤 User ID: <code>{w['user_id']}</code>\n"
                f"💰 Amount: <b>{w['amount']} {TOKEN_NAME}</b>\n"
                f"👛 Wallet: <code>{escape(w['wallet'])}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Mark Paid", callback_data=f"wd_pay_{wid}"), InlineKeyboardButton("❌ Reject & Refund", callback_data=f"wd_ref_{wid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_withdrawals")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("wd_pay_"):
        wid = data.replace("wd_pay_", "", 1)
        try:
            withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": "paid"}})
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
            if w:
                try:
                    await context.bot.send_message(w["user_id"], f"✅ Your withdrawal of <b>{w['amount']} {TOKEN_NAME}</b> has been paid!", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("✅ Marked as paid!", show_alert=True)
        except Exception:
            await query.answer("❌ Error.", show_alert=True)
        return

    if data.startswith("wd_ref_"):
        wid = data.replace("wd_ref_", "", 1)
        try:
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
            if w and w.get("status") == "pending":
                withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": "rejected"}})
                users_collection.update_one({"user_id": w["user_id"]}, {"$inc": {"balance": w["amount"]}})
                try:
                    await context.bot.send_message(w["user_id"], f"❌ Your withdrawal of <b>{w['amount']} {TOKEN_NAME}</b> was rejected and refunded.", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("✅ Rejected and refunded!", show_alert=True)
        except Exception:
            await query.answer("❌ Error.", show_alert=True)
        return

    if data == "admin_submissions":
        pending_subs = list(submissions_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending_subs:
            await query.edit_message_text(
                "📥 <b>Pending Submissions</b>\n\nNo pending submissions.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
                parse_mode="HTML"
            )
            return

        buttons = [[InlineKeyboardButton(f"User: {sub['user_id']}", callback_data=f"sub_manage_{sub['_id']}")] for sub in pending_subs]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("📥 <b>Pending Submissions</b>\n\nSelect a submission to review:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("sub_manage_"):
        sid = data.replace("sub_manage_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
        except Exception:
            sub = None

        if not sub:
            await query.answer("❌ Submission not found.", show_alert=True)
            return

        task = tasks_collection.find_one({"task_id": sub["task_id"]})
        task_name = task.get("title", "Unknown") if task else "Unknown"
        proof = escape(str(sub.get("proof", "")))

        await query.edit_message_text(
            (
                "📥 <b>Submission Details</b>\n\n"
                f"👤 User ID: <code>{sub['user_id']}</code>\n"
                f"🎯 Task: <b>{escape(str(task_name))}</b>\n"
                f"📄 Proof: <code>{proof}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"sub_approve_{sid}"), InlineKeyboardButton("❌ Reject", callback_data=f"sub_reject_{sid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_submissions")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("sub_approve_"):
        sid = data.replace("sub_approve_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
            if sub and sub.get("status") == "pending":
                submissions_collection.update_one({"_id": ObjectId(sid)}, {"$set": {"status": "approved"}})
                task = tasks_collection.find_one({"task_id": sub["task_id"]})
                if task:
                    reward = float(task.get("reward", 0))
                    users_collection.update_one({"user_id": sub["user_id"]}, {"$inc": {"balance": reward}, "$addToSet": {"completed_tasks": sub["task_id"]}})
                    await check_and_pay_referral(sub["user_id"], context)
                    try:
                        await context.bot.send_message(sub["user_id"], f"✅ Your manual task proof was approved! <b>+{reward} {TOKEN_NAME}</b> added.", parse_mode="HTML")
                    except Exception:
                        pass
            await query.answer("✅ Approved!", show_alert=True)
        except Exception:
            pass
        return

    if data.startswith("sub_reject_"):
        sid = data.replace("sub_reject_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
            if sub and sub.get("status") == "pending":
                submissions_collection.update_one({"_id": ObjectId(sid)}, {"$set": {"status": "rejected"}})
                try:
                    await context.bot.send_message(sub["user_id"], "❌ Your manual task proof was rejected by admin.", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("❌ Rejected!", show_alert=True)
        except Exception:
            pass
        return

    if data == "admin_users":
        total = users_collection.count_documents({})
        await query.edit_message_text(
            f"👥 <b>User Management</b>\n\nTotal Users: <b>{total}</b>",
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

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "admin_broadcast_msg"
        await query.edit_message_text("📢 <b>Broadcast Message</b>\n\nSend the message you want to broadcast to all users:", parse_mode="HTML")
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


async def admin_text_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return False
    action = context.user_data.get("admin_action")
    text = update.message.text.strip()

    if action == "admin_broadcast_msg":
        context.user_data.clear()
        all_users = users_collection.find({})
        count = 0
        await update.message.reply_text("📢 Broadcasting started...")
        for u in all_users:
            try:
                await context.bot.send_message(u["user_id"], text, parse_mode="HTML")
                count += 1
            except Exception:
                pass
        await update.message.reply_text(f"✅ Broadcast complete. Sent to {count} users.")
        return True

    if action == "task_add_title":
        context.user_data["new_task"]["title"] = text
        context.user_data["admin_action"] = "task_add_desc"
        await update.message.reply_text("📝 Send task description:")
        return True

    if action == "task_add_desc":
        context.user_data["new_task"]["description"] = text
        context.user_data["admin_action"] = "task_add_link"
        await update.message.reply_text("🔗 Send task link (URL/Channel link):")
        return True

    if action == "task_add_link":
        context.user_data["new_task"]["link"] = text
        context.user_data["admin_action"] = "task_add_reward"
        await update.message.reply_text(f"💰 Send reward amount (e.g., 0.1):")
        return True

    if action == "task_add_reward":
        try:
            reward = float(text)
            context.user_data["new_task"]["reward"] = reward
            context.user_data["admin_action"] = "task_add_slots"
            await update.message.reply_text("👥 Send total slots (number, e.g. 100):")
        except Exception:
            await update.message.reply_text("❌ Invalid number. Send reward amount again:")
        return True

    if action == "task_add_slots":
        try:
            slots = int(text)
            ntask = context.user_data.get("new_task")
            tid = str(uuid.uuid4())[:8]
            
            tasks_collection.insert_one({
                "task_id": tid,
                "title": ntask["title"],
                "description": ntask["description"],
                "link": ntask["link"],
                "reward": ntask["reward"],
                "total_slots": slots,
                "completed_slots": 0,
                "task_type": "auto",
                "active": True,
                "created_at": datetime.now(timezone.utc)
            })
            context.user_data.clear()
            await update.message.reply_text("✅ Task added successfully!")
        except Exception:
            await update.message.reply_text("❌ Error saving task. Send total slots again:")
        return True

    if action == "user_ban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} banned.")
        except Exception:
            await update.message.reply_text("❌ Invalid User ID.")
        return True

    if action == "user_unban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} unbanned.")
        except Exception:
            await update.message.reply_text("❌ Invalid User ID.")
        return True

    if action == "balance_add":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Added {amount} POL to user {target_id}")
        except Exception:
            await update.message.reply_text("❌ Format: USER_ID AMOUNT")
        return True

    if action == "balance_remove":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Removed {amount} POL from user {target_id}")
        except Exception:
            await update.message.reply_text("❌ Format: USER_ID AMOUNT")
        return True

    if action == "balance_check":
        try:
            target_id = int(text)
            target_user = get_user(target_id)
            bal = float(target_user.get("balance", 0.0)) if target_user else 0.0
            context.user_data.clear()
            await update.message.reply_text(f"🔎 User <code>{target_id}</code> Balance: <b>{bal:.6f} {TOKEN_NAME}</b>", parse_mode="HTML")
        except Exception:
            await update.message.reply_text("❌ User not found.")
        return True

    return False
        
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if db_user and db_user.get("is_banned", False):
        return

    if text == "🎯 Tasks":
        await tasks_menu(update, context)
    elif text == "💰 Balance":
        await balance_menu(update, context)
    elif text == "💳 Withdraw":
        await withdraw_menu(update, context)
    elif text == "👥 Refer":
        await refer_menu(update, context)
    elif text == "👑 Admin Panel" and user_id == ADMIN_ID:
        await admin_panel(update, context)


async def photo_proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task_id = context.user_data.get("submitting_task_id")
    if not task_id:
        return

    proof = update.message.caption.strip() if update.message.caption else "Screenshot proof"
    submissions_collection.insert_one({
        "user_id": user_id,
        "task_id": task_id,
        "proof": proof,
        "photo_file_id": update.message.photo[-1].file_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc)
    })
    context.user_data.pop("submitting_task_id", None)
    await update.message.reply_text("✅ Screenshot proof submitted successfully!")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get("submitting_task_id"):
        proof_text = update.message.text.strip()
        task_id = context.user_data.pop("submitting_task_id")
        submissions_collection.insert_one({
            "user_id": user_id,
            "task_id": task_id,
            "proof": proof_text,
            "status": "pending",
            "created_at": datetime.now(timezone.utc)
        })
        await update.message.reply_text("✅ Proof submitted successfully!")
        return

    if context.user_data.get("admin_action") and user_id == ADMIN_ID:
        if await admin_text_action(update, context):
            return

    if context.user_data.get("withdraw_step"):
        await process_withdraw(update, context)
        return

    await menu_handler(update, context)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), InlineKeyboardButton("🎯 Manage Tasks", callback_data="admin_tasks")],
        [InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals"), InlineKeyboardButton("📥 Submissions", callback_data="admin_submissions")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"), InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("👑 <b>Admin Panel</b>\n\nSelect an option:", reply_markup=admin_keyboard(), parse_mode="HTML")
                
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Unauthorized.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_home":
        await query.edit_message_text("👑 <b>Admin Panel</b>\n\nSelect an option:", reply_markup=admin_keyboard(), parse_mode="HTML")
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
                f"🟢 Active: <b>{active}</b>\n"
                f"🔴 Inactive: <b>{inactive}</b>"
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
        context.user_data["admin_action"] = "task_add_title"
        context.user_data["new_task"] = {}
        await query.edit_message_text("➕ <b>Add New Task</b>\n\nSend task title:", parse_mode="HTML")
        return

    if data == "task_list_admin":
        try:
            tasks = list(tasks_collection.find({}).sort("created_at", -1).limit(10))
            if not tasks:
                await query.edit_message_text(
                    "📋 <b>Task List</b>\n\nNo tasks found.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")]]),
                    parse_mode="HTML"
                )
                return
            buttons = []
            for t in tasks:
                status = "🟢" if t.get("active") else "🔴"
                buttons.append([
                    InlineKeyboardButton(f"{status} {t.get('title')}", callback_data=f"admintask_view_{t['task_id']}")
                ])
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
            await query.edit_message_text("📋 <b>Task List (Click to Delete/Manage)</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        except Exception as e:
            print("Task list error:", e)
            try:
                await query.answer("❌ Error loading tasks.", show_alert=True)
            except Exception:
                pass
        return
        
        

    if data.startswith("admintask_view_"):
        tid = data.replace("admintask_view_", "", 1)
        t = tasks_collection.find_one({"task_id": tid})
        if not t:
            await query.answer("❌ Task not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                f"🎯 <b>Task: {escape(str(t.get('title')))}</b>\n\n"
                f"📝 Desc: {escape(str(t.get('description')))}\n"
                f"🔗 Link: {escape(str(t.get('link')))}\n"
                f"💰 Reward: {t.get('reward')} {TOKEN_NAME}\n"
                f"👥 Slots: {t.get('completed_slots', 0)}/{t.get('total_slots', 0)}"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Delete Task", callback_data=f"admintask_del_{tid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="task_list_admin")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("admintask_del_"):
        tid = data.replace("admintask_del_", "", 1)
        tasks_collection.delete_one({"task_id": tid})
        await query.answer("✅ Task deleted successfully!", show_alert=True)
        
        active = tasks_collection.count_documents({"active": True})
        inactive = tasks_collection.count_documents({"active": False})
        await query.edit_message_text(
            (
                "🎯 <b>Manage Tasks & Slots</b>\n\n"
                f"🟢 Active: <b>{active}</b>\n"
                f"🔴 Inactive: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Task (with Slots)", callback_data="task_add")],
                [InlineKeyboardButton("📋 View/Delete Tasks", callback_data="task_list_admin")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "admin_withdrawals":
        pending_w = list(withdrawals_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending_w:
            await query.edit_message_text(
                "💳 <b>Pending Withdrawals</b>\n\nNo pending withdrawals.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
                parse_mode="HTML"
            )
            return
        buttons = [[InlineKeyboardButton(f"User: {w['user_id']} - {w['amount']} POL", callback_data=f"wd_manage_{w['_id']}")] for w in pending_w]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("💳 <b>Pending Withdrawals</b>\n\nSelect to manage:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("wd_manage_"):
        wid = data.replace("wd_manage_", "", 1)
        try:
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        except Exception:
            w = None
        if not w:
            await query.answer("❌ Withdrawal not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                "💳 <b>Withdrawal Details</b>\n\n"
                f"👤 User ID: <code>{w['user_id']}</code>\n"
                f"💰 Amount: <b>{w['amount']} {TOKEN_NAME}</b>\n"
                f"👛 Wallet: <code>{escape(w['wallet'])}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Mark Paid", callback_data=f"wd_pay_{wid}"), InlineKeyboardButton("❌ Reject & Refund", callback_data=f"wd_ref_{wid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_withdrawals")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("wd_pay_"):
        wid = data.replace("wd_pay_", "", 1)
        try:
            withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": "paid"}})
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
            if w:
                try:
                    await context.bot.send_message(w["user_id"], f"✅ Your withdrawal of <b>{w['amount']} {TOKEN_NAME}</b> has been paid!", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("✅ Marked as paid!", show_alert=True)
        except Exception:
            await query.answer("❌ Error.", show_alert=True)
        return

    if data.startswith("wd_ref_"):
        wid = data.replace("wd_ref_", "", 1)
        try:
            w = withdrawals_collection.find_one({"_id": ObjectId(wid)})
            if w and w.get("status") == "pending":
                withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": "rejected"}})
                users_collection.update_one({"user_id": w["user_id"]}, {"$inc": {"balance": w["amount"]}})
                try:
                    await context.bot.send_message(w["user_id"], f"❌ Your withdrawal of <b>{w['amount']} {TOKEN_NAME}</b> was rejected and refunded.", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("✅ Rejected and refunded!", show_alert=True)
        except Exception:
            await query.answer("❌ Error.", show_alert=True)
        return

    if data == "admin_submissions":
        pending_subs = list(submissions_collection.find({"status": "pending"}).sort("created_at", -1).limit(10))
        if not pending_subs:
            await query.edit_message_text(
                "📥 <b>Pending Submissions</b>\n\nNo pending submissions.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
                parse_mode="HTML"
            )
            return

        buttons = [[InlineKeyboardButton(f"User: {sub['user_id']}", callback_data=f"sub_manage_{sub['_id']}")] for sub in pending_subs]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("📥 <b>Pending Submissions</b>\n\nSelect a submission to review:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("sub_manage_"):
        sid = data.replace("sub_manage_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
        except Exception:
            sub = None

        if not sub:
            await query.answer("❌ Submission not found.", show_alert=True)
            return

        task = tasks_collection.find_one({"task_id": sub["task_id"]})
        task_name = task.get("title", "Unknown") if task else "Unknown"
        proof = escape(str(sub.get("proof", "")))

        await query.edit_message_text(
            (
                "📥 <b>Submission Details</b>\n\n"
                f"👤 User ID: <code>{sub['user_id']}</code>\n"
                f"🎯 Task: <b>{escape(str(task_name))}</b>\n"
                f"📄 Proof: <code>{proof}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"sub_approve_{sid}"), InlineKeyboardButton("❌ Reject", callback_data=f"sub_reject_{sid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_submissions")]
            ]),
            parse_mode="HTML"
        )
        return

    if data.startswith("sub_approve_"):
        sid = data.replace("sub_approve_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
            if sub and sub.get("status") == "pending":
                submissions_collection.update_one({"_id": ObjectId(sid)}, {"$set": {"status": "approved"}})
                task = tasks_collection.find_one({"task_id": sub["task_id"]})
                if task:
                    reward = float(task.get("reward", 0))
                    users_collection.update_one({"user_id": sub["user_id"]}, {"$inc": {"balance": reward}, "$addToSet": {"completed_tasks": sub["task_id"]}})
                    await check_and_pay_referral(sub["user_id"], context)
                    try:
                        await context.bot.send_message(sub["user_id"], f"✅ Your manual task proof was approved! <b>+{reward} {TOKEN_NAME}</b> added.", parse_mode="HTML")
                    except Exception:
                        pass
            await query.answer("✅ Approved!", show_alert=True)
        except Exception:
            pass
        return

    if data.startswith("sub_reject_"):
        sid = data.replace("sub_reject_", "", 1)
        try:
            sub = submissions_collection.find_one({"_id": ObjectId(sid)})
            if sub and sub.get("status") == "pending":
                submissions_collection.update_one({"_id": ObjectId(sid)}, {"$set": {"status": "rejected"}})
                try:
                    await context.bot.send_message(sub["user_id"], "❌ Your manual task proof was rejected by admin.", parse_mode="HTML")
                except Exception:
                    pass
            await query.answer("❌ Rejected!", show_alert=True)
        except Exception:
            pass
        return

    if data == "admin_users":
        total = users_collection.count_documents({})
        await query.edit_message_text(
            f"👥 <b>User Management</b>\n\nTotal Users: <b>{total}</b>",
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
        min_w = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))
        ref_comm = float(get_setting("ref_commission", DEFAULT_REF_COMMISSION))
        await query.edit_message_text(
            (
                "⚙️ <b>Bot Settings</b>\n\n"
                f"📌 Min Withdraw: <b>{min_w} {TOKEN_NAME}</b>\n"
                f"🎁 Ref Commission: <b>{ref_comm} {TOKEN_NAME}</b>\n\n"
                "Choose an option to change:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Change Min Withdraw", callback_data="set_min_withdraw")],
                [InlineKeyboardButton("✏️ Change Ref Commission", callback_data="set_ref_comm")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "set_min_withdraw":
        context.user_data["admin_action"] = "update_min_withdraw"
        await query.edit_message_text("📌 Send new minimum withdraw amount (e.g., 1.5):", parse_mode="HTML")
        return

    if data == "set_ref_comm":
        context.user_data["admin_action"] = "update_ref_comm"
        await query.edit_message_text("🎁 Send new referral commission amount (e.g., 0.5):", parse_mode="HTML")
        return

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "admin_broadcast_msg"
        await query.edit_message_text("📢 <b>Broadcast Message</b>\n\nSend the message you want to broadcast to all users:", parse_mode="HTML")
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


async def admin_text_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return False
    action = context.user_data.get("admin_action")
    text = update.message.text.strip()

    if action == "admin_broadcast_msg":
        context.user_data.clear()
        all_users = users_collection.find({})
        count = 0
        await update.message.reply_text("📢 Broadcasting started...")
        for u in all_users:
            try:
                await context.bot.send_message(u["user_id"], text, parse_mode="HTML")
                count += 1
            except Exception:
                pass
        await update.message.reply_text(f"✅ Broadcast complete. Sent to {count} users.")
        return True

    if action == "task_add_title":
        context.user_data["new_task"]["title"] = text
        context.user_data["admin_action"] = "task_add_desc"
        await update.message.reply_text("📝 Send task description:")
        return True

    if action == "task_add_desc":
        context.user_data["new_task"]["description"] = text
        context.user_data["admin_action"] = "task_add_link"
        await update.message.reply_text("🔗 Send task link (URL/Channel link):")
        return True

    if action == "task_add_link":
        context.user_data["new_task"]["link"] = text
        context.user_data["admin_action"] = "task_add_reward"
        await update.message.reply_text(f"💰 Send reward amount (e.g., 0.1):")
        return True

    if action == "task_add_reward":
        try:
            reward = float(text)
            context.user_data["new_task"]["reward"] = reward
            context.user_data["admin_action"] = "task_add_slots"
            await update.message.reply_text("👥 Send total slots (number, e.g. 100):")
        except Exception:
            await update.message.reply_text("❌ Invalid number. Send reward amount again:")
        return True

    if action == "task_add_slots":
        try:
            slots = int(text)
            ntask = context.user_data.get("new_task")
            tid = str(uuid.uuid4())[:8]
            
            tasks_collection.insert_one({
                "task_id": tid,
                "title": ntask["title"],
                "description": ntask["description"],
                "link": ntask["link"],
                "reward": ntask["reward"],
                "total_slots": slots,
                "completed_slots": 0,
                "task_type": "auto",
                "active": True,
                "created_at": datetime.now(timezone.utc)
            })
            context.user_data.clear()
            await update.message.reply_text("✅ Task added successfully!")
        except Exception:
            await update.message.reply_text("❌ Error saving task. Send total slots again:")
        return True

    if action == "update_min_withdraw":
        try:
            val = float(text)
            update_setting("min_withdraw", val)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Min withdraw updated to {val} {TOKEN_NAME}")
        except Exception:
            await update.message.reply_text("❌ Invalid number. Send valid amount:")
        return True

    if action == "update_ref_comm":
        try:
            val = float(text)
            update_setting("ref_commission", val)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Referral commission updated to {val} {TOKEN_NAME}")
        except Exception:
            await update.message.reply_text("❌ Invalid number. Send valid amount:")
        return True

    if action == "user_ban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} banned.")
        except Exception:
            await update.message.reply_text("❌ Invalid User ID.")
        return True

    if action == "user_unban_action":
        try:
            uid = int(text)
            users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ User {uid} unbanned.")
        except Exception:
            await update.message.reply_text("❌ Invalid User ID.")
        return True

    if action == "balance_add":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Added {amount} POL to user {target_id}")
        except Exception:
            await update.message.reply_text("❌ Format: USER_ID AMOUNT")
        return True

    if action == "balance_remove":
        try:
            parts = text.split()
            target_id, amount = int(parts[0]), float(parts[1])
            users_collection.update_one({"user_id": target_id, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}})
            context.user_data.clear()
            await update.message.reply_text(f"✅ Removed {amount} POL from user {target_id}")
        except Exception:
            await update.message.reply_text("❌ Format: USER_ID AMOUNT")
        return True

    if action == "balance_check":
        try:
            target_id = int(text)
            target_user = get_user(target_id)
            bal = float(target_user.get("balance", 0.0)) if target_user else 0.0
            context.user_data.clear()
            await update.message.reply_text(f"🔎 User <code>{target_id}</code> Balance: <b>{bal:.6f} {TOKEN_NAME}</b>", parse_mode="HTML")
        except Exception:
            await update.message.reply_text("❌ User not found.")
        return True

    return False
        
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if db_user and db_user.get("is_banned", False):
        return

    if text == "🎯 Tasks":
        await tasks_menu(update, context)
    elif text == "💰 Balance":
        await balance_menu(update, context)
    elif text == "💳 Withdraw":
        await withdraw_menu(update, context)
    elif text == "👥 Refer":
        await refer_menu(update, context)
    elif text == "👑 Admin Panel" and user_id == ADMIN_ID:
        await admin_panel(update, context)


async def photo_proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task_id = context.user_data.get("submitting_task_id")
    if not task_id:
        return

    proof = update.message.caption.strip() if update.message.caption else "Screenshot proof"
    submissions_collection.insert_one({
        "user_id": user_id,
        "task_id": task_id,
        "proof": proof,
        "photo_file_id": update.message.photo[-1].file_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc)
    })
    context.user_data.pop("submitting_task_id", None)
    await update.message.reply_text("✅ Screenshot proof submitted successfully!")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get("submitting_task_id"):
        proof_text = update.message.text.strip()
        task_id = context.user_data.pop("submitting_task_id")
        submissions_collection.insert_one({
            "user_id": user_id,
            "task_id": task_id,
            "proof": proof_text,
            "status": "pending",
            "created_at": datetime.now(timezone.utc)
        })
        await update.message.reply_text("✅ Proof submitted successfully!")
        return

    if context.user_data.get("admin_action") and user_id == ADMIN_ID:
        if await admin_text_action(update, context):
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

    if user_id == ADMIN_ID and any(data.startswith(p) for p in ["admin_", "task_add", "task_list", "admintask_", "sub_", "wd_", "user_", "balance_", "set_"]):
        await admin_callback(update, context)
        return
        


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
    application.add_handler(MessageHandler(filters.PHOTO, photo_proof_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)

    print("TaskMint Bot is running with all updated features...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
            
