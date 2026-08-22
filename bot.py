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

def create_or_update_user(user):

    now = datetime.now(timezone.utc)

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
                "referred_by": None,
                "completed_tasks": [],
                "created_at": now
            }
        },
        upsert=True
    )


def get_user(user_id):

    return users_collection.find_one(
        {"user_id": user_id}
    )


def get_balance(user_id):

    user = get_user(user_id)

    if not user:
        return 0.0

    return float(
        user.get("balance", 0.0)
    )


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(user_id):

    buttons = [
        ["🎯 Tasks"],
        ["💰 Balance", "💳 Withdraw"],
        ["👥 Refer"],
    ]

    if user_id == ADMIN_ID:
        buttons.append(
            ["👑 Admin Panel"]
        )

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True
    )


# =========================================================
# JOIN BUTTON
# =========================================================

def join_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url=CHANNEL_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Done",
                callback_data="check_join"
            )
        ]
    ])


# =========================================================
# CHANNEL CHECK
# =========================================================

async def is_channel_member(
    bot,
    user_id
):

    try:

        member = await bot.get_chat_member(
            REQUIRED_CHANNEL,
            user_id
        )

        return member.status in [
            "member",
            "administrator",
            "creator"
        ]

    except Exception as error:

        print(
            "Channel check error:",
            error
        )

        return False


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    create_or_update_user(user)

    context.user_data.clear()

    await update.message.reply_text(
        "Checking...",
        reply_markup=ReplyKeyboardRemove()
    )

    member = await is_channel_member(
        context.bot,
        user.id
    )

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

    await send_main_menu(
        context.bot,
        user.id
    )


# =========================================================
# MAIN MENU
# =========================================================

async def send_main_menu(
    bot,
    user_id
):

    await bot.send_message(
        chat_id=user_id,
        text=(
            "🎉 <b>Welcome to TaskMint!</b>\n\n"
            "Choose an option below."
        ),
        reply_markup=main_menu(user_id),
        parse_mode="HTML"
    )


# =========================================================
# DONE / JOIN VERIFY
# =========================================================

async def check_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user_id = query.from_user.id

    member = await is_channel_member(
        context.bot,
        user_id
    )

    if not member:

        await query.answer(
            "❌ Please join the channel first.",
            show_alert=True
        )

        return

    await query.answer(
        "✅ Verified!"
    )

    await query.edit_message_text(
        (
            "✅ <b>Done!</b>\n\n"
            "Your membership has been verified."
        ),
        parse_mode="HTML"
    )

    await send_main_menu(
        context.bot,
        user_id
    )


# =========================================================
# BALANCE
# =========================================================

async def balance_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    balance = get_balance(user_id)

    await update.message.reply_text(
        (
            "💰 <b>Your Balance</b>\n\n"
            f"💎 Balance: <b>{balance:.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        parse_mode="HTML"
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    tasks = list(
        tasks_collection.find(
            {"active": True}
        ).sort("created_at", -1)
    )

    if not tasks:

        await update.message.reply_text(
            (
                "🎯 <b>Tasks</b>\n\n"
                "No tasks are available right now."
            ),
            parse_mode="HTML"
        )

        return

    buttons = []

    for task in tasks:

        buttons.append([
            InlineKeyboardButton(
                task.get(
                    "title",
                    "Task"
                ),
                callback_data=(
                    f"task_{task['task_id']}"
                )
            )
        ])

    await update.message.reply_text(
        "🎯 <b>Available Tasks</b>",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML"
    )


# =========================================================
# TASK DETAILS
# =========================================================

async def task_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    task_id = query.data.replace(
        "task_",
        "",
        1
    )

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:

        await query.answer(
            "❌ Task not found.",
            show_alert=True
        )

        return

    keyboard = []

    link = task.get("link")

    if link:

        keyboard.append([
            InlineKeyboardButton(
                "🔗 Open Task",
                url=link
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "✅ Complete",
            callback_data=f"complete_{task_id}"
        )
    ])

    await query.answer()

    await query.edit_message_text(
        (
            f"🎯 <b>{task.get('title', 'Task')}</b>\n\n"
            f"📝 {task.get('description', '')}\n\n"
            f"💰 Reward: <b>"
            f"{float(task.get('reward', 0)):.6f} "
            f"{TOKEN_NAME}</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML"
    )


# =========================================================
# COMPLETE TASK
# =========================================================

async def complete_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user_id = query.from_user.id

    task_id = query.data.replace(
        "complete_",
        "",
        1
    )

    task = tasks_collection.find_one(
        {
            "task_id": task_id,
            "active": True
        }
    )

    if not task:

        await query.answer(
            "❌ Task is no longer available.",
            show_alert=True
        )

        return

    reward = float(
        task.get("reward", 0)
    )

    already_done = users_collection.find_one(
        {
            "user_id": user_id,
            "completed_tasks": task_id
        }
    )

    if already_done:

        await query.answer(
            "❌ You already completed this task.",
            show_alert=True
        )

        return

    users_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "balance": reward
            },
            "$addToSet": {
                "completed_tasks": task_id
            }
        }
    )

    await query.answer(
        "✅ Task completed!",
        show_alert=True
    )

    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward: <b>+{reward:.6f} "
            f"{TOKEN_NAME}</b>\n\n"
            "The reward has been added "
            "to your balance."
        ),
        parse_mode="HTML"
    )


# =========================================================
# REFER
# =========================================================

async def refer_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    user = get_user(user_id)

    referrals = (
        user.get("referrals", 0)
        if user else 0
    )

    bot_info = await context.bot.get_me()

    referral_link = (
        f"https://t.me/"
        f"{bot_info.username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        (
            "👥 <b>Refer & Earn</b>\n\n"
            f"👤 Referrals: <b>{referrals}</b>\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>"
        ),
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW
# =========================================================

async def withdraw_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    balance = get_balance(user_id)

    context.user_data.clear()

    if balance < MIN_WITHDRAW:

        await update.message.reply_text(
            (
                "💳 <b>POL Withdrawal</b>\n\n"
                f"💰 Balance: "
                f"<b>{balance:.6f} POL</b>\n"
                f"📌 Minimum: "
                f"<b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
                "❌ Insufficient balance."
            ),
            parse_mode="HTML"
        )

        return

    context.user_data[
        "withdraw_step"
    ] = "amount"

    await update.message.reply_text(
        (
            "💳 <b>POL Withdrawal</b>\n\n"
            f"💰 Available: "
            f"<b>{balance:.6f} POL</b>\n"
            f"📌 Minimum: "
            f"<b>{MIN_WITHDRAW:.6f} POL</b>\n\n"
            "Enter withdrawal amount:"
        ),
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW PROCESS
# =========================================================

async def process_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    step = context.user_data.get(
        "withdraw_step"
    )

    if step == "amount":

        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Enter a valid amount."
            )
            return

        balance = get_balance(user_id)

        if amount < MIN_WITHDRAW:
            await update.message.reply_text(
                f"❌ Minimum is {MIN_WITHDRAW} POL."
            )
            return

        if amount > balance:
            await update.message.reply_text(
                "❌ Insufficient balance."
            )
            return

        context.user_data[
            "withdraw_amount"
        ] = amount

        context.user_data[
            "withdraw_step"
        ] = "wallet"

        await update.message.reply_text(
            (
                "👛 <b>POL Wallet</b>\n\n"
                "Send your POL wallet address."
            ),
            parse_mode="HTML"
        )

        return

    if step == "wallet":

        wallet = text

        amount = context.user_data.get(
            "withdraw_amount"
        )

        if not amount:
            context.user_data.clear()
            return

        result = users_collection.update_one(
            {
                "user_id": user_id,
                "balance": {
                    "$gte": amount
                }
            },
            {
                "$inc": {
                    "balance": -amount
                }
            }
        )

        if result.modified_count != 1:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Insufficient balance."
            )

            return

        now = datetime.now(
            timezone.utc
        )

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

            result = (
                withdrawals_collection.insert_one(
                    withdrawal
                )
            )

        except PyMongoError:

            users_collection.update_one(
                {"user_id": user_id},
                {
                    "$inc": {
                        "balance": amount
                    }
                }
            )

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Withdrawal failed."
            )

            return

        wid = str(
            result.inserted_id
        )

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

            print(
                "Admin notification:",
                error
            )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "🎯 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            ),
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Balance Management",
                callback_data="admin_balance"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            ),
            InlineKeyboardButton(
                "⚙️ Settings",
                callback_data="admin_settings"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_back"
            )
        ]
    ])


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        (
            "👑 <b>Admin Panel</b>\n\n"
            "Select an option:"
        ),
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
        )
# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "🎯 Manage Tasks",
                callback_data="admin_tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Withdrawals",
                callback_data="admin_withdrawals"
            ),
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Balance Management",
                callback_data="admin_balance"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast"
            ),
            InlineKeyboardButton(
                "⚙️ Settings",
                callback_data="admin_settings"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_back"
            )
        ]
    ])


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        (
            "👑 <b>Admin Panel</b>\n\n"
            "Select an option:"
        ),
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    # -----------------------------------------------------
    # ADMIN HOME
    # -----------------------------------------------------

    if data == "admin_home":

        await query.edit_message_text(
            "👑 <b>Admin Panel</b>\n\nSelect an option:",
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # STATISTICS
    # -----------------------------------------------------

    if data == "admin_stats":

        total_users = (
            users_collection.count_documents({})
        )

        total_tasks = (
            tasks_collection.count_documents({})
        )

        pending = (
            withdrawals_collection.count_documents(
                {"status": "pending"}
            )
        )

        approved = (
            withdrawals_collection.count_documents(
                {"status": "approved"}
            )
        )

        rejected = (
            withdrawals_collection.count_documents(
                {"status": "rejected"}
            )
        )

        total_paid = list(
            withdrawals_collection.aggregate([
                {
                    "$match": {
                        "status": "approved"
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total": {
                            "$sum": "$amount"
                        }
                    }
                }
            ])
        )

        paid_amount = (
            float(total_paid[0]["total"])
            if total_paid
            else 0
        )

        await query.edit_message_text(
            (
                "📊 <b>Statistics</b>\n\n"
                f"👥 Total Users: "
                f"<b>{total_users}</b>\n"
                f"🎯 Total Tasks: "
                f"<b>{total_tasks}</b>\n"
                f"🕐 Pending Withdrawals: "
                f"<b>{pending}</b>\n"
                f"✅ Approved Withdrawals: "
                f"<b>{approved}</b>\n"
                f"❌ Rejected Withdrawals: "
                f"<b>{rejected}</b>\n"
                f"💰 Total Paid: "
                f"<b>{paid_amount:.6f} POL</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # MANAGE TASKS
    # -----------------------------------------------------

    if data == "admin_tasks":

        active = tasks_collection.count_documents(
            {"active": True}
        )

        inactive = tasks_collection.count_documents(
            {"active": False}
        )

        await query.edit_message_text(
            (
                "🎯 <b>Manage Tasks</b>\n\n"
                f"🟢 Active Tasks: <b>{active}</b>\n"
                f"🔴 Inactive Tasks: <b>{inactive}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Add Task",
                        callback_data="task_add"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📋 Active Tasks",
                        callback_data="task_list"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑 Delete Task",
                        callback_data="task_delete"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # WITHDRAWALS
    # -----------------------------------------------------

    if data == "admin_withdrawals":

        pending = list(
            withdrawals_collection.find(
                {"status": "pending"}
            ).sort(
                "created_at",
                -1
            ).limit(10)
        )

        if not pending:

            await query.edit_message_text(
                (
                    "💳 <b>Withdrawals</b>\n\n"
                    "No pending withdrawals."
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="admin_home"
                        )
                    ]
                ]),
                parse_mode="HTML"
            )

            return

        buttons = []

        for item in pending:

            wid = str(
                item["_id"]
            )

            amount = float(
                item.get("amount", 0)
            )

            buttons.append([
                InlineKeyboardButton(
                    f"💰 {amount:.4f} POL",
                    callback_data=f"withdraw_{wid}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_home"
            )
        ])

        await query.edit_message_text(
            (
                "💳 <b>Pending Withdrawals</b>\n\n"
                "Select a withdrawal:"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # USERS
    # -----------------------------------------------------

    if data == "admin_users":

        total = users_collection.count_documents({})

        await query.edit_message_text(
            (
                "👥 <b>User Management</b>\n\n"
                f"Total Users: <b>{total}</b>\n\n"
                "User management options:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Search User",
                        callback_data="user_search"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Ban User",
                        callback_data="user_ban"
                    ),
                    InlineKeyboardButton(
                        "🔓 Unban User",
                        callback_data="user_unban"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # BALANCE MANAGEMENT
    # -----------------------------------------------------

    if data == "admin_balance":

        await query.edit_message_text(
            (
                "💰 <b>Balance Management</b>\n\n"
                "Manage user POL balance:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Add POL",
                        callback_data="balance_add"
                    ),
                    InlineKeyboardButton(
                        "➖ Remove POL",
                        callback_data="balance_remove"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔎 Check Balance",
                        callback_data="balance_check"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return
    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    if data == "admin_broadcast":

        context.user_data[
            "admin_action"
        ] = "broadcast"

        await query.edit_message_text(
            (
                "📢 <b>Broadcast</b>\n\n"
                "Send the message you want to "
                "broadcast to all users.\n\n"
                "Send /cancel to cancel."
            ),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # SETTINGS
    # -----------------------------------------------------

    if data == "admin_settings":

        await query.edit_message_text(
            (
                "⚙️ <b>Bot Settings</b>\n\n"
                f"🪙 Token: <b>{TOKEN_NAME}</b>\n"
                f"💳 Minimum Withdraw: "
                f"<b>{MIN_WITHDRAW} POL</b>\n"
                f"📢 Channel: "
                f"<b>{REQUIRED_CHANNEL}</b>"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="admin_home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return


# =========================================================
# BALANCE ACTION CALLBACK
# =========================================================

async def balance_action_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "❌ Unauthorized.",
            show_alert=True
        )

        return

    data = query.data

    await query.answer()

    if data == "balance_add":

        context.user_data[
            "admin_action"
        ] = "balance_add"

        await query.edit_message_text(
            (
                "➕ <b>Add POL</b>\n\n"
                "Send:\n"
                "<code>USER_ID AMOUNT</code>\n\n"
                "Example:\n"
                "<code>123456789 10</code>"
            ),
            parse_mode="HTML"
        )

        return

    if data == "balance_remove":

        context.user_data[
            "admin_action"
        ] = "balance_remove"

        await query.edit_message_text(
            (
                "➖ <b>Remove POL</b>\n\n"
                "Send:\n"
                "<code>USER_ID AMOUNT</code>"
            ),
            parse_mode="HTML"
        )

        return

    if data == "balance_check":

        context.user_data[
            "admin_action"
        ] = "balance_check"

        await query.edit_message_text(
            (
                "🔎 <b>Check Balance</b>\n\n"
                "Send the User ID."
            ),
            parse_mode="HTML"
        )

        return


# =========================================================
# ADMIN TEXT ACTION
# =========================================================

async def admin_text_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return False

    action = context.user_data.get(
        "admin_action"
    )

    text = update.message.text.strip()

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    if action == "broadcast":

        if text == "/cancel":

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Broadcast cancelled."
            )

            return True

        users = users_collection.find(
            {},
            {"user_id": 1}
        )

        sent = 0
        failed = 0

        for user in users:

            try:

                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text
                )

                sent += 1

            except Exception:

                failed += 1

        context.user_data.clear()

        await update.message.reply_text(
            (
                "📢 <b>Broadcast Finished</b>\n\n"
                f"✅ Sent: <b>{sent}</b>\n"
                f"❌ Failed: <b>{failed}</b>"
            ),
            parse_mode="HTML"
        )

        return True

    # -----------------------------------------------------
    # ADD BALANCE
    # -----------------------------------------------------

    if action == "balance_add":

        try:

            parts = text.split()

            target_id = int(parts[0])
            amount = float(parts[1])

            if amount <= 0:
                raise ValueError

        except Exception:

            await update.message.reply_text(
                (
                    "❌ Invalid format.\n\n"
                    "Use:\n"
                    "<code>USER_ID AMOUNT</code>\n\n"
                    "Example:\n"
                    "<code>123456789 10</code>"
                ),
                parse_mode="HTML"
            )

            return True

        result = users_collection.update_one(
            {"user_id": target_id},
            {
                "$inc": {
                    "balance": amount
                }
            }
        )

        context.user_data.clear()

        if result.matched_count == 0:

            await update.message.reply_text(
                "❌ User not found."
            )

        else:

            await update.message.reply_text(
                (
                    "✅ <b>Balance Added</b>\n\n"
                    f"👤 User: <code>{target_id}</code>\n"
                    f"💰 Added: <b>+{amount:.6f} POL</b>\n"
                    f"💎 Balance: "
                    f"<b>{get_balance(target_id):.6f} POL</b>"
                ),
                parse_mode="HTML"
            )

        return True

    # -----------------------------------------------------
    # REMOVE BALANCE
    # -----------------------------------------------------

    if action == "balance_remove":

        try:

            parts = text.split()

            target_id = int(parts[0])
            amount = float(parts[1])

            if amount <= 0:
                raise ValueError

        except Exception:

            await update.message.reply_text(
                (
                    "❌ Invalid format.\n\n"
                    "Use:\n"
                    "<code>USER_ID AMOUNT</code>"
                ),
                parse_mode="HTML"
            )

            return True

        result = users_collection.update_one(
            {
                "user_id": target_id,
                "balance": {
                    "$gte": amount
                }
            },
            {
                "$inc": {
                    "balance": -amount
                }
            }
        )

        context.user_data.clear()

        if result.modified_count != 1:

            await update.message.reply_text(
                "❌ User not found or insufficient balance."
            )

        else:

            await update.message.reply_text(
                (
                    "✅ <b>Balance Removed</b>\n\n"
                    f"👤 User: <code>{target_id}</code>\n"
                    f"💰 Removed: <b>-{amount:.6f} POL</b>\n"
                    f"💎 Balance: "
                    f"<b>{get_balance(target_id):.6f} POL</b>"
                ),
                parse_mode="HTML"
            )

        return True

    # -----------------------------------------------------
    # CHECK BALANCE
    # -----------------------------------------------------

    if action == "balance_check":

        try:

            target_id = int(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Send a valid User ID."
            )

            return True

        user = get_user(target_id)

        context.user_data.clear()

        if not user:

            await update.message.reply_text(
                "❌ User not found."
            )

            return True

        await update.message.reply_text(
            (
                "💰 <b>User Balance</b>\n\n"
                f"👤 User ID: <code>{target_id}</code>\n"
                f"💎 Balance: "
                f"<b>{get_balance(target_id):.6f} POL</b>\n"
                f"👥 Referrals: "
                f"<b>{user.get('referrals', 0)}</b>"
            ),
            parse_mode="HTML"
        )

        return True

    return False


# =========================================================
# MENU HANDLER
# =========================================================

async def menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text

    member = await is_channel_member(
        context.bot,
        user_id
    )

    if not member:

        await update.message.reply_text(
            "🔐 Please join the channel first.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "👇 Join and press Done:",
            reply_markup=join_keyboard()
        )

        return

    # Admin actions
    if user_id == ADMIN_ID:

        handled = await admin_text_action(
            update,
            context
        )

        if handled:
            return

    if text == "🎯 Tasks":

        await tasks_menu(
            update,
            context
        )

    elif text == "💰 Balance":

        await balance_menu(
            update,
            context
        )

    elif text == "💳 Withdraw":

        await withdraw_menu(
            update,
            context
        )

    elif text == "👥 Refer":

        await refer_menu(
            update,
            context
        )

    elif text == "👑 Admin Panel":

        await admin_panel(
            update,
            context
        )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if context.user_data.get(
        "withdraw_step"
    ):

        await process_withdraw(
            update,
            context
        )

        return

    await menu_handler(
        update,
        context
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    data = update.callback_query.data

    if data == "check_join":

        await check_join(
            update,
            context
        )

        return

    if data.startswith("task_"):

        await task_details(
            update,
            context
        )

        return

    if data.startswith("complete_"):

        await complete_task(
            update,
            context
        )

        return

    if data.startswith("balance_"):

        await balance_action_callback(
            update,
            context
        )

        return

    if data.startswith("admin_"):

        await admin_callback(
            update,
            context
        )

        return


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        repr(context.error)
    )


# =========================================================
# MAIN
# =========================================================

def main():

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    print(
        "Connecting to MongoDB..."
    )

    mongo_client.admin.command(
        "ping"
    )

    print(
        "MongoDB connected successfully."
    )

    setup_database()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
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
        "TaskMint Bot is running..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
