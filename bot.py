import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient
from pymongo.errors import PyMongoError

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
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(
            b"TaskMint Bot is running!"
        )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server started on port {port}"
    )

    server.serve_forever()


# =========================================================
# MONGODB
# =========================================================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000
)

db = mongo_client["taskmint"]

users_collection = db["users"]
tasks_collection = db["tasks"]
withdrawals_collection = db["withdrawals"]


def setup_database():

    users_collection.create_index(
        "user_id",
        unique=True
    )

    tasks_collection.create_index(
        "task_id",
        unique=True
    )

    withdrawals_collection.create_index(
        "user_id"
    )

    withdrawals_collection.create_index(
        "status"
    )

    print("MongoDB database ready.")
    
# =========================================================
# USER FUNCTIONS
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
                    {
                        "$inc": {"referrals": 1, "balance": 0.1}
                    }
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

    await update.message.reply_text(
        "Checking...",
        reply_markup=ReplyKeyboardRemove()
    )

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
        text=(
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option below."
        ),
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
        ("✅ <b>Done!</b>\n\nYour membership has been verified."),
        parse_mode="HTML"
    )
    await send_main_menu(context.bot, user_id)
               
# =========================================================
# BALANCE, TASKS & REFERRAL MENUS
# =========================================================

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
        await update.message.reply_text(
            ("🎯 <b>Tasks</b>\n\nNo tasks are available right now."),
            parse_mode="HTML"
        )
        return

    buttons = []
    for task in tasks:
        buttons.append([
            InlineKeyboardButton(
                task.get("title", "Task"),
                callback_data=f"task_{task['task_id']}"
            )
        ])

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
        await query.answer("❌ Task not found.", show_alert=True)
        return

    keyboard = []
    link = task.get("link")
    if link:
        keyboard.append([InlineKeyboardButton("🔗 Open Task", url=link)])

    keyboard.append([InlineKeyboardButton("✅ Complete", callback_data=f"complete_{task_id}")])

    await query.answer()
    await query.edit_message_text(
        (
            f"🎯 <b>{task.get('title', 'Task')}</b>\n\n"
            f"📝 {task.get('description', '')}\n\n"
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

    reward = float(task.get("reward", 0))
    already_done = users_collection.find_one({"user_id": user_id, "completed_tasks": task_id})

    if already_done:
        await query.answer("❌ You already completed this task.", show_alert=True)
        return

    users_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {"balance": reward},
            "$addToSet": {"completed_tasks": task_id}
        }
    )

    await query.answer("✅ Task completed!", show_alert=True)
    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward: <b>+{reward:.6f} {TOKEN_NAME}</b>\n\n"
            "The reward has been added to your balance."
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
        await update.message.reply_text(
            ("👛 <b>POL Wallet</b>\n\nSend your POL wallet address."),
            parse_mode="HTML"
        )
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
            
# =========================================================
# ADMIN PANEL & CALLBACKS
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("🎯 Manage Tasks", callback_data="admin_tasks")
        ],
        [
            InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals"),
            InlineKeyboardButton("👥 Users", callback_data="admin_users")
        ],
        [InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance")],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")
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
        pending = withdrawals_collection.count_documents({"status": "pending"})
        approved = withdrawals_collection.count_documents({"status": "approved"})
        rejected = withdrawals_collection.count_documents({"status": "rejected"})

        total_paid = list(
            withdrawals_collection.aggregate([
                {"$match": {"status": "approved"}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ])
        )
        paid_amount = float(total_paid[0]["total"]) if total_paid else 0

        await query.edit_message_text(
            (
                "📊 <b>Statistics</b>\n\n"
                f"👥 Total Users: <b>{total_users}</b>\n"
                f"🎯 Total Tasks: <b>{total_tasks}</b>\n"
                f"🕐 Pending Withdrawals: <b>{pending}</b>\n"
                f"✅ Approved Withdrawals: <b>{approved}</b>\n"
                f"❌ Rejected Withdrawals: <b>{rejected}</b>\n"
                f"💰 Total Paid: <b>{paid_amount:.6f} POL</b>"
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
                "🎯 <b>Manage Tasks</b>\n\n"
                f"🟢 Active Tasks: <b>{active}</b>\n"
                f"🔴 Inactive Tasks: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Task", callback_data="task_add")],
                [InlineKeyboardButton("📋 Active Tasks", callback_data="task_list_admin")],
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
        tasks = list(tasks_collection.find({"active": True}))
        if not tasks:
            await query.edit_message_text("No active tasks.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")]]))
            return
        buttons = []
        for t in tasks:
            buttons.append([InlineKeyboardButton(f"❌ Delete: {t['title']}", callback_data=f"admin_deltask_{t['task_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")])
        await query.edit_message_text("Select a task to delete/deactivate:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin_deltask_"):
        tid = data.replace("admin_deltask_", "")
        tasks_collection.update_one({"task_id": tid}, {"$set": {"active": False}})
        await query.edit_message_text("✅ Task deactivated successfully.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_tasks")]]))
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
            buttons.append([InlineKeyboardButton(f"💰 {amount:.4f} POL - {item['user_id']}", callback_data=f"withdraw_manage_{wid}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
        await query.edit_message_text("💳 <b>Pending Withdrawals</b>\n\nSelect a withdrawal to approve/reject:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if data.startswith("withdraw_manage_"):
        wid = data.replace("withdraw_manage_", "")
        from bson import ObjectId
        try:
            wd = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        except:
            wd = None
        if not wd:
            await query.answer("❌ Withdrawal not found.", show_alert=True)
            return
        await query.edit_message_text(
            (
                f"💳 <b>Withdrawal Details</b>\n\n"
                f"🆔 ID: <code>{wid}</code>\n"
                f"👤 User: <code>{wd['user_id']}</code>\n"
                f"💰 Amount: <b>{wd['amount']} POL</b>\n"
                f"👛 Wallet: <code>{wd['wallet']}</code>\n"
                f"📌 Status: <b>{wd['status']}</b>"
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
        from bson import ObjectId
        action_type, wid = data.split("_")[1], data.split("_")[2]
        new_status = "approved" if action_type == "approve" else "rejected"
        wd = withdrawals_collection.find_one({"_id": ObjectId(wid)})
        if wd and wd["status"] == "pending":
            withdrawals_collection.update_one({"_id": ObjectId(wid)}, {"$set": {"status": new_status}})
            if new_status == "rejected":
                users_collection.update_one({"user_id": wd["user_id"]}, {"$inc": {"balance": wd["amount"]}})
                try:
                    await context.bot.send_message(wd["user_id"], f"❌ Your withdrawal of {wd['amount']} POL has been rejected & refunded.")
                except:
                    pass
            else:
                try:
                    await context.bot.send_message(wd["user_id"], f"✅ Your withdrawal of {wd['amount']} POL has been approved!")
                except:
                    pass
            await query.edit_message_text(f"✅ Withdrawal marked as {new_status}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_withdrawals")]]))
        else:
            await query.answer("Already processed or not found.", show_alert=True)
        return

    if data == "admin_users":
        total = users_collection.count_documents({})
        await query.edit_message_text(
            (
                "👥 <b>User Management</b>\n\n"
                f"Total Users: <b>{total}</b>\n\n"
                "User management options:"
            ),
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
        await query.edit_message_text("🚫 Send the User ID to ban:", parse_mode="HTML")
        return

    if data == "user_unban":
        context.user_data["admin_action"] = "user_unban_action"
        await query.edit_message_text("🔓 Send the User ID to unban:", parse_mode="HTML")
        return

    if data == "admin_balance":
        await query.edit_message_text(
            ("💰 <b>Balance Management</b>\n\nManage user POL balance:"),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Add POL", callback_data="balance_add"),
                    InlineKeyboardButton("➖ Remove POL", callback_data="balance_remove")
                ],
                [InlineKeyboardButton("🔎 Check Balance", callback_data="balance_check")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
            ]),
            parse_mode="HTML"
        )
        return

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.edit_message_text(
            (
                "📢 <b>Broadcast</b>\n\n"
                "Send the message you want to broadcast to all users.\n\n"
                "Send /cancel to cancel."
            ),
            parse_mode="HTML"
        )
        return

    if data == "admin_settings":
        await query.edit_message_text(
            (
                "⚙️ <b>Bot Settings</b>\n\n"
                f"🪙 Token: <b>{TOKEN_NAME}</b>\n"
                f"💳 Minimum Withdraw: <b>{MIN_WITHDRAW} POL</b>\n"
                f"📢 Channel: <b>{REQUIRED_CHANNEL}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]),
            parse_mode="HTML"
        )
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
        await query.edit_message_text("🔎 <b>Check Balance</b>\n\nSend the User ID.", parse_mode="HTML")
        return
    
