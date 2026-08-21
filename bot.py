import os
import threading
import re
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import pymongo

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

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI", "YOUR_MONGODB_URI_HERE")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

TASK_REWARD = 10
REFERRAL_REWARD = 20
DAILY_REWARD = 10
MIN_WITHDRAW = 100

AMOUNT, METHOD, ACCOUNT = range(3)

# =========================
# MONGODB DATABASE SETUP
# =========================

client = pymongo.MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client["taskmint_bot_db"]

users_col = db["users"]
completed_tasks_col = db["completed_tasks"]
withdrawals_col = db["withdrawals"]
channel_tasks_col = db["channel_tasks"]
settings_col = db["settings"]

DEFAULT_SETTINGS = {
    "button_earn": "💰 Earn Tasks",
    "button_referral": "👥 Refer & Earn",
    "button_withdraw": "💳 Withdraw",
    "button_daily": "🎁 Daily Bonus",
    "button_balance": "📊 My Balance",
    "button_help": "ℹ️ Help",
    "feature_earn": "1",
    "feature_referral": "1",
    "feature_withdraw": "1",
    "feature_daily": "1",
    "feature_balance": "1",
    "feature_help": "1",
    "reward_task": "10",
    "reward_referral": "20",
    "reward_daily": "10",
    "min_withdraw": "100",
}

BUTTON_KEYS = [
    ("earn", "Earn Tasks"),
    ("referral", "Refer & Earn"),
    ("withdraw", "Withdraw"),
    ("daily", "Daily Bonus"),
    ("balance", "My Balance"),
    ("help", "Help"),
]

def init_db():
    for key, value in DEFAULT_SETTINGS.items():
        if not settings_col.find_one({"key": key}):
            settings_col.insert_one({"key": key, "value": value})

def get_setting(key):
    row = settings_col.find_one({"key": key})
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

def set_setting(key, value):
    settings_col.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)

def setting_int(key, fallback):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return fallback

def feature_on(feature):
    return get_setting(f"feature_{feature}") == "1"
    
# =========================
# DYNAMIC MENU & USER HELPERS
# =========================

def get_markup():
    rows = []
    current = []
    for key in ("earn", "referral", "withdraw", "daily", "balance", "help"):
        if feature_on(key):
            current.append(get_setting(f"button_{key}"))
            if len(current) == 2:
                rows.append(current)
                current = []
    if current:
        rows.append(current)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def register_user(user, referred_by=None):
    existing = users_col.find_one({"user_id": user.id})
    if not existing:
        users_col.insert_one({
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "points": 0,
            "referred_by": referred_by,
            "referral_rewarded": 0,
            "last_bonus": ""
        })
    else:
        users_col.update_one(
            {"user_id": user.id},
            {"$set": {"username": user.username or "", "first_name": user.first_name or ""}}
        )

def get_user(user_id):
    return users_col.find_one({"user_id": user_id})

def points(user_id):
    user = get_user(user_id)
    return user["points"] if user else 0

def add_points(user_id, amount):
    users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})

def remove_points(user_id, amount):
    user = get_user(user_id)
    if user:
        new_p = max(user["points"] - amount, 0)
        users_col.update_one({"user_id": user_id}, {"$set": {"points": new_p}})

def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID

# =========================
# COMMANDS & FEATURES
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if context.args:
        try:
            ref_id = int(context.args[0])
            if ref_id != user.id:
                referred_by = ref_id
        except ValueError:
            referred_by = None

    register_user(user, referred_by)
    if referred_by:
        await process_referral(user.id)

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn points.\n"
        "👥 Invite friends and earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Withdraw your earned points.\n\n"
        "👇 Select an option from the menu.",
        reply_markup=get_markup()
    )

def create_default_task():
    if channel_tasks_col.count_documents({}) == 0:
        channel_tasks_col.insert_one({
            "task_id": 1,
            "title": "Join Official Channel",
            "channel": "@Telegram",
            "channel_url": "https://t.me/Telegram",
            "reward": setting_int("reward_task", TASK_REWARD),
            "active": 1
        })

def get_channel_tasks():
    return list(channel_tasks_col.find({"active": 1}))

def get_channel_task(task_id):
    return channel_tasks_col.find_one({"task_id": task_id, "active": 1})

def task_done(user_id, task_key):
    return completed_tasks_col.find_one({"user_id": user_id, "task_key": task_key}) is not None

def save_task(user_id, task_key):
    completed_tasks_col.update_one(
        {"user_id": user_id, "task_key": task_key},
        {"$set": {"user_id": user_id, "task_key": task_key}},
        upsert=True
    )

async def earn_tasks(update, context):
    if not feature_on("earn"):
        await update.message.reply_text("⚠️ Earn Tasks feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    rows = get_channel_tasks()
    if not rows:
        await update.message.reply_text("💰 EARN TASKS\n\n😔 বর্তমানে কোনো Task available নেই।", reply_markup=get_markup())
        return

    buttons = []
    for row in rows:
        t_id = row["task_id"]
        buttons.append([InlineKeyboardButton(f"📢 {row['title']} (+{row['reward']} Points)", url=row["channel_url"])])
        buttons.append([InlineKeyboardButton(f"✅ Check Task #{t_id}", callback_data=f"check_task_{t_id}")])

    await update.message.reply_text(
        "💰 EARN TASKS\n\n📌 Task complete করার নিয়ম:\n1️⃣ প্রথমে Channel Join করো।\n2️⃣ Join করার পর নিচের ✅ Check button চাপো।",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def task_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not data.startswith("check_task_"):
        return

    try:
        task_id = int(data.split("_")[2])
    except (ValueError, IndexError):
        await query.answer("❌ Invalid Task", show_alert=True)
        return

    task = get_channel_task(task_id)
    if not task:
        await query.edit_message_text("❌ এই Task আর available নেই।")
        return

    task_key = f"channel_{task_id}"
    if task_done(user_id, task_key):
        await query.edit_message_text(f"⚠️ TASK ALREADY COMPLETED\n\n💰 Current Points: {points(user_id)}")
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel"], user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            add_points(user_id, task["reward"])
            save_task(user_id, task_key)
            await query.edit_message_text(f"🎉 TASK COMPLETED!\n\n✅ Reward: +{task['reward']} Points\n💰 Total Points: {points(user_id)}")
        else:
            await query.edit_message_text("❌ TASK NOT COMPLETED\n\nআগে Channel-এ Join করো।")
    except Exception as e:
        print("Task check error:", e)
        await query.edit_message_text("⚠️ Task verify করা যাচ্ছে না।")
async def refer_earn(update, context):
    if not feature_on("referral"):
        await update.message.reply_text("⚠️ Referral feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    user = update.effective_user
    bot = await context.bot.get_me()
    referral_link = f"https://t.me/{bot.username}?start={user.id}"
    referrals = users_col.count_documents({"referred_by": user.id, "referral_rewarded": 1})
    reward = setting_int("reward_referral", REFERRAL_REWARD)

    await update.message.reply_text(
        f"👥 REFER & EARN\n\n🎁 প্রতি successful referral: +{reward} Points\n👥 Total Referrals: {referrals}\n\n🔗 Link:\n{referral_link}",
        reply_markup=get_markup()
    )

async def process_referral(user_id):
    user = get_user(user_id)
    if not user or not user.get("referred_by") or user.get("referral_rewarded") == 1 or user.get("referred_by") == user_id:
        return

    referrer_id = user["referred_by"]
    reward = setting_int("reward_referral", REFERRAL_REWARD)
    add_points(referrer_id, reward)
    users_col.update_one({"user_id": user_id}, {"$set": {"referral_rewarded": 1}})

async def daily_bonus(update, context):
    if not feature_on("daily"):
        await update.message.reply_text("⚠️ Daily Bonus feature এখন বন্ধ আছে।", reply_markup=get_markup())
        return

    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        register_user(update.effective_user)
        user = get_user(user_id)

    today = datetime.now().strftime("%Y-%m-%d")
    if user.get("last_bonus") == today:
        await update.message.reply_text(f"🎁 DAILY BONUS\n\n⏳ আজকের bonus ক্লেইম করেছ।\n💰 Points: {points(user_id)}", reply_markup=get_markup())
        return

    reward = setting_int("reward_daily", DAILY_REWARD)
    add_points(user_id, reward)
    users_col.update_one({"user_id": user_id}, {"$set": {"last_bonus": today}})
    await update.message.reply_text(f"🎉 DAILY BONUS CLAIMED!\n\n🎁 +{reward} Points\n💰 Total Points: {points(user_id)}", reply_markup=get_markup())

# =========================
# WITHDRAW SYSTEM
# =========================

async def withdraw_start(update, context):
    if not feature_on("withdraw"):
        await update.message.reply_text("⚠️ Withdraw feature বন্ধ।", reply_markup=get_markup())
        return ConversationHandler.END

    context.user_data.clear()
    user_id = update.effective_user.id
    balance = points(user_id)
    minimum = setting_int("min_withdraw", MIN_WITHDRAW)

    if balance < minimum:
        await update.message.reply_text(f"💳 WITHDRAW\n\n💰 Balance: {balance}\n📌 Minimum: {minimum}\n❌ পর্যাপ্ত ব্যালেন্স নেই।", reply_markup=get_markup())
        return ConversationHandler.END

    await update.message.reply_text(f"💳 WITHDRAW\n\n💰 Balance: {balance}\n📌 Minimum: {minimum}\n\nকত Points withdraw করতে চাও?")
    return AMOUNT

async def withdraw_amount(update, context):
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ শুধু সংখ্যায় Amount পাঠাও।")
        return AMOUNT

    minimum = setting_int("min_withdraw", MIN_WITHDRAW)
    balance = points(user_id)

    if amount < minimum or amount > balance:
        await update.message.reply_text("❌ ইনভ্যালিড Amount। আবার পাঠাও।")
        return AMOUNT

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text("💳 Payment Method পাঠাও (Bkash/Nagad/Binance):")
    return METHOD

async def withdraw_method(update, context):
    context.user_data["withdraw_method"] = update.message.text.strip()
    await update.message.reply_text("📱 Payment Account Number/ID পাঠাও:")
    return ACCOUNT

async def withdraw_account(update, context):
    account = update.message.text.strip()
    user = update.effective_user
    amount = context.user_data.get("withdraw_amount")
    method = context.user_data.get("withdraw_method")

    if not amount or not method or amount > points(user.id):
        await update.message.reply_text("❌ Session expired.", reply_markup=get_markup())
        return ConversationHandler.END

    remove_points(user.id, amount)
    w_id = withdrawals_col.count_documents({}) + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    withdrawals_col.insert_one({
        "req_id": w_id,
        "user_id": user.id,
        "username": user.username or "",
        "amount": amount,
        "method": method,
        "account": account,
        "status": "pending",
        "created_at": now
    })

    await update.message.reply_text(f"✅ WITHDRAWAL REQUEST SENT!\n\n🆔 Request: #{w_id}\n💰 Amount: {amount}\n💳 Method: {method}\n📱 Account: {account}", reply_markup=get_markup())
    
    if ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 NEW WITHDRAWAL #{w_id}\n👤 User ID: {user.id}\n💰 Amount: {amount}\n💳 Method: {method}\n📱 Account: {account}")
        except Exception:
            pass

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=get_markup())
    return ConversationHandler.END
    
async def my_balance(update, context):
    if not feature_on("balance"):
        await update.message.reply_text("⚠️ Balance feature বন্ধ।", reply_markup=get_markup())
        return

    user_id = update.effective_user.id
    user = get_user(user_id) or {}
    referrals = users_col.count_documents({"referred_by": user_id, "referral_rewarded": 1})
    completed_tasks = completed_tasks_col.count_documents({"user_id": user_id})

    await update.message.reply_text(
        f"📊 MY BALANCE\n\n💰 Points: {user.get('points', 0)}\n👥 Referrals: {referrals}\n✅ Completed Tasks: {completed_tasks}",
        reply_markup=get_markup()
    )

async def help_menu(update, context):
    await update.message.reply_text("ℹ️ HELP & INFORMATION\n\nবটের যেকোনো সমস্যায় অ্যাডমিনের সাথে যোগাযোগ করুন।", reply_markup=get_markup())

# =========================
# MENU BUTTON ROUTER (FIXED)
# =========================

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_setting("button_earn"):
        await earn_tasks(update, context)
    elif text == get_setting("button_referral"):
        await refer_earn(update, context)
    elif text == get_setting("button_daily"):
        await daily_bonus(update, context)
    elif text == get_setting("button_balance"):
        await my_balance(update, context)
    elif text == get_setting("button_help"):
        await help_menu(update, context)

# =========================
# SERVER & MAIN
# =========================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running on MongoDB!")

def web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing.")

    init_db()
    create_default_task()

    threading.Thread(target=web_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    withdraw_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^" + re.escape(get_setting("button_withdraw")) + "$"), withdraw_start)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_method)],
            ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_account)],
        },
        fallbacks=[CommandHandler("cancel", withdraw_cancel)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(withdraw_conv)
    app.add_handler(CallbackQueryHandler(task_callback, pattern="^check_task_"))
    
    # বাটন প্রসেস করার হ্যান্ডলার যুক্ত করা হলো
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))

    print("TaskMint Bot started with MongoDB!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
