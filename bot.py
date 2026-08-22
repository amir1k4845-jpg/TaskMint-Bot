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
MIN_WITHDRAW = 1.0
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
# MONGODB COLLECTIONS
# =========================================================

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]
submissions_collection = db["task_submissions"]


def setup_database():
    users_collection.create_index("user_id", unique=True)
    tasks_collection.create_index("task_id", unique=True)
    withdrawals_collection.create_index("user_id")
    withdrawals_collection.create_index("status")
    submissions_collection.create_index("user_id")
    submissions_collection.create_index("task_id")
    print("MongoDB database ready.")
    
# =========================================================
# USER FUNCTIONS & MENUS
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
                    {"$inc": {"referrals": 1, "balance": 0.1}}
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

        buttons.append([
            InlineKeyboardButton(
                f"{task.get('title', 'Task')} (Rem: {remaining if total_slots > 0 else '∞'})",
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
        keyboard.append([InlineKeyboardButton("✅ Complete (Auto)", callback_data=f"complete_{task_id}")])
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

    await query.answer("✅ Task completed!", show_alert=True)
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
            "📤 <b>Submit Task Proof</b>\n\n"
            f"Task: <b>{task.get('title')}</b>\n\n"
            "Please send your proof (Text, Username, or Screenshot link) in the next message:"
        ),
        parse_mode="HTML"
    )


async def refer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    referrals = user.get("referrals", 0) if user else 0
    bot_info = await context.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start={user_id}"

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{referrals}</b>\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>"
        ),
        parse_mode="HTML"
    )


async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    context.user_data.clear()

    if balance < MIN_WITHDRAW:
        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: <b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: <b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
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
            f"📌 Minimum: <b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
            "Enter withdrawal amount:"
        ),
        parse_mode="HTML"
    )


async def process_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    step = context.user_data.get("withdraw_step")

    if step == "amount":
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("❌ Enter a valid amount.")
            return

        balance = get_balance(user_id)
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ Minimum is {MIN_WITHDRAW} POL.")
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

        try:
            await context.bot.send_message(
                ADMIN_ID,
                (
                    "🔔 <b>New Withdrawal</b>\n\n"
                    f"🆔 <code>{wid}</code>\n"
                    f"👤 User: <code>{user_id}</code>\n"
                    f"💰 Amount: <b>{amount:.6f} POL</b>\n"
                    f"👛 Wallet: <code>{wallet}</code>"
                ),
                parse_mode="HTML"
            )
        except Exception as error:
            print("Admin notification:", error)
    
