import os
import threading
import re
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from motor.motor_asyncio import AsyncIOMotorClient

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

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGO_URI = os.getenv("MONGO_URI", "YOUR_MONGODB_URI_HERE")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7003609983"))

# Default crypto rewards (POL token)
TASK_REWARD = 0.5
REFERRAL_REWARD = 1.0
DAILY_REWARD = 0.1
MIN_WITHDRAW = 5.0

AMOUNT, METHOD, ACCOUNT = range(3)

# =========================
# MONGODB ASYNC SETUP
# =========================

client = AsyncIOMotorClient(MONGO_URI, tls=True)
db = client["taskmint_bot_db"]

users_col = db["users"]
completed_tasks_col = db["completed_tasks"]
withdrawals_col = db["withdrawals"]
channel_tasks_col = db["channel_tasks"]
settings_col = db["settings"]

DEFAULT_SETTINGS = {
    "button_earn": "💰 Earn POL",
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
    "reward_task": str(TASK_REWARD),
    "reward_referral": str(REFERRAL_REWARD),
    "reward_daily": str(DAILY_REWARD),
    "min_withdraw": str(MIN_WITHDRAW),
}

SETTINGS_CACHE = {}

async def load_settings():
    global SETTINGS_CACHE
    async for setting in settings_col.find({}):
        SETTINGS_CACHE[setting["key"]] = setting["value"]
    
    for key, value in DEFAULT_SETTINGS.items():
        if key not in SETTINGS_CACHE:
            SETTINGS_CACHE[key] = value
            await settings_col.insert_one({"key": key, "value": value})

def get_setting(key):
    return SETTINGS_CACHE.get(key, DEFAULT_SETTINGS.get(key, ""))

def setting_float(key, fallback):
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        return fallback

def feature_on(feature):
    return get_setting(f"feature_{feature}") == "1"

# =========================
# USER HELPERS & MENUS
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

def get_menu_buttons_list():
    return [get_setting(f"button_{k}") for k in ("earn", "referral", "withdraw", "daily", "balance", "help")]

async def register_user(user, referred_by=None):
    existing = await users_col.find_one({"user_id": user.id})
    if not existing:
        await users_col.insert_one({
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "balance": 0.0,
            "referred_by": referred_by,
            "referral_rewarded": 0,
            "last_bonus": ""
        })

async def get_user(user_id):
    return await users_col.find_one({"user_id": user_id})

async def get_balance(user_id):
    user = await get_user(user_id)
    return float(user.get("balance", 0.0)) if user else 0.0

async def add_balance(user_id, amount):
    await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": float(amount)}})

async def remove_balance(user_id, amount):
    user = await get_user(user_id)
    if user:
        new_b = max(float(user.get("balance", 0.0)) - float(amount), 0.0)
        await users_col.update_one({"user_id": user_id}, {"$set": {"balance": new_b}})

async def create_default_task():
    count = await channel_tasks_col.count_documents({})
    if count == 0:
        await channel_tasks_col.insert_one({
            "task_id": 1,
            "title": "Join Official Channel",
            "channel": "@Telegram",
            "channel_url": "https://t.me/Telegram",
            "reward": setting_float("reward_task", TASK_REWARD),
            "active": 1
        })
        
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
            pass

    await register_user(user, referred_by)
    if referred_by:
        await process_referral(user.id)

    await update.message.reply_text(
        "👋 Welcome to TaskMint Bot!\n\n"
        "💰 Complete tasks and earn POL tokens.\n"
        "👥 Invite friends and earn referral rewards.\n"
        "🎁 Claim your daily bonus.\n"
        "💳 Withdraw your earned POL instantly.\n\n"
        "👇 Select an option from the menu below.",
        reply_markup=get_markup()
    )

async def earn_tasks(update, context):
    if not feature_on("earn"):
        await update.message.reply_text("⚠️ Earn Tasks is currently disabled.", reply_markup=get_markup())
        return

    cursor = channel_tasks_col.find({"active": 1})
    rows = await cursor.to_list(length=100)
    
    if not rows:
        await update.message.reply_text("💰 EARN POL\n\n😔 No tasks are available right now.", reply_markup=get_markup())
        return

    buttons = []
    for row in rows:
        t_id = row["task_id"]
        buttons.append([InlineKeyboardButton(f"📢 {row['title']} (+{row['reward']} POL)", url=row['channel_url'])])
        buttons.append([InlineKeyboardButton(f"✅ Check Task #{t_id}", callback_data=f"check_task_{t_id}")])

    await update.message.reply_text(
        "💰 EARN POL\n\n📌 How to complete tasks:\n1️⃣ Join the Channel.\n2️⃣ Click the ✅ Check button below.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def task_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not data.startswith("check_task_"):
        return

    task_id = int(data.split("_")[2])
    task = await channel_tasks_col.find_one({"task_id": task_id, "active": 1})
    
    if not task:
        await query.edit_message_text("❌ This task is no longer available.")
        return

    task_key = f"channel_{task_id}"
    completed = await completed_tasks_col.find_one({"user_id": user_id, "task_key": task_key})
    
    if completed:
        balance = await get_balance(user_id)
        await query.edit_message_text(f"⚠️ TASK ALREADY COMPLETED\n\n💰 Current Balance: {balance:.2f} POL")
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel"], user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            await add_balance(user_id, task["reward"])
            await completed_tasks_col.update_one(
                {"user_id": user_id, "task_key": task_key},
                {"$set": {"user_id": user_id, "task_key": task_key}},
                upsert=True
            )
            balance = await get_balance(user_id)
            await query.edit_message_text(f"🎉 TASK COMPLETED!\n\n✅ Reward: +{task['reward']} POL\n💰 Total Balance: {balance:.2f} POL")
        else:
            await query.edit_message_text("❌ TASK NOT COMPLETED\n\nPlease join the channel first.")
    except Exception:
        await query.edit_message_text("⚠️ Cannot verify task. Make sure the bot is an admin in the channel.")

async def process_referral(user_id):
    user = await get_user(user_id)
    if not user or not user.get("referred_by") or user.get("referral_rewarded") == 1:
        return

    referrer_id = user["referred_by"]
    reward = setting_float("reward_referral", REFERRAL_REWARD)
    await add_balance(referrer_id, reward)
    await users_col.update_one({"user_id": user_id}, {"$set": {"referral_rewarded": 1}})

async def refer_earn(update, context):
    user = update.effective_user
    bot = await context.bot.get_me()
    referral_link = f"https://t.me/{bot.username}?start={user.id}"
    referrals = await users_col.count_documents({"referred_by": user.id, "referral_rewarded": 1})
    reward = setting_float("reward_referral", REFERRAL_REWARD)

    await update.message.reply_text(
        f"👥 REFER & EARN\n\n🎁 Reward per invite: +{reward} POL\n👥 Total Referrals: {referrals}\n\n🔗 Your Link:\n{referral_link}",
        reply_markup=get_markup()
    )

async def daily_bonus(update, context):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if user and user.get("last_bonus") == today:
        balance = await get_balance(user_id)
        await update.message.reply_text(f"⏳ You already claimed today's bonus!\n💰 Balance: {balance:.2f} POL", reply_markup=get_markup())
        return

    reward = setting_float("reward_daily", DAILY_REWARD)
    await add_balance(user_id, reward)
    await users_col.update_one({"user_id": user_id}, {"$set": {"last_bonus": today}})
    
    new_balance = await get_balance(user_id)
    await update.message.reply_text(f"🎉 DAILY BONUS CLAIMED!\n\n🎁 +{reward} POL\n💰 Total Balance: {new_balance:.2f} POL", reply_markup=get_markup())

async def my_balance(update, context):
    user_id = update.effective_user.id
    balance = await get_balance(user_id)
    referrals = await users_col.count_documents({"referred_by": user_id, "referral_rewarded": 1})
    completed_tasks = await completed_tasks_col.count_documents({"user_id": user_id})

    await update.message.reply_text(
        f"📊 MY BALANCE\n\n💰 Available: {balance:.2f} POL\n👥 Referrals: {referrals}\n✅ Completed Tasks: {completed_tasks}",
        reply_markup=get_markup()
    )

async def help_menu(update, context):
    await update.message.reply_text("ℹ️ HELP & INFO\n\nContact the administrator for support.", reply_markup=get_markup())
            
# =========================
# WITHDRAWAL SYSTEM
# =========================

async def withdraw_start(update, context):
    context.user_data.clear()
    user_id = update.effective_user.id
    balance = await get_balance(user_id)
    minimum = setting_float("min_withdraw", MIN_WITHDRAW)

    if balance < minimum:
        await update.message.reply_text(f"💳 WITHDRAW\n\n💰 Balance: {balance:.2f} POL\n📌 Minimum: {minimum:.2f} POL\n❌ Insufficient balance.", reply_markup=get_markup())
        return ConversationHandler.END

    await update.message.reply_text(f"💳 WITHDRAW\n\n💰 Balance: {balance:.2f} POL\n📌 Minimum: {minimum:.2f} POL\n\nHow many POL do you want to withdraw? (Numbers only)")
    return AMOUNT

async def withdraw_amount(update, context):
    text = update.message.text.strip()
    if text in get_menu_buttons_list():
        await update.message.reply_text("❌ Cancelled.", reply_markup=get_markup())
        return ConversationHandler.END

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ Valid number required.")
        return AMOUNT

    balance = await get_balance(update.effective_user.id)
    minimum = setting_float("min_withdraw", MIN_WITHDRAW)

    if amount < minimum or amount > balance:
        await update.message.reply_text(f"❌ Amount must be between {minimum:.2f} and {balance:.2f}.")
        return AMOUNT

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text("💳 Send withdrawal method (e.g., Polygon Network, Binance):")
    return METHOD

async def withdraw_method(update, context):
    text = update.message.text.strip()
    if text in get_menu_buttons_list():
        await update.message.reply_text("❌ Cancelled.", reply_markup=get_markup())
        return ConversationHandler.END

    context.user_data["withdraw_method"] = text
    await update.message.reply_text("📱 Send your Wallet Address:")
    return ACCOUNT

async def withdraw_account(update, context):
    account = update.message.text.strip()
    if account in get_menu_buttons_list():
        await update.message.reply_text("❌ Cancelled.", reply_markup=get_markup())
        return ConversationHandler.END

    user = update.effective_user
    amount = context.user_data.get("withdraw_amount")
    method = context.user_data.get("withdraw_method")

    await remove_balance(user.id, amount)
    w_id = await withdrawals_col.count_documents({}) + 1

    await withdrawals_col.insert_one({
        "req_id": w_id, "user_id": user.id, "amount": amount, 
        "method": method, "account": account, "status": "pending"
    })

    await update.message.reply_text(f"✅ REQUEST SENT!\n\n🆔 #{w_id}\n💰 Amount: {amount:.2f} POL\n📱 Address: {account}", reply_markup=get_markup())
    
    if ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{w_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{w_id}")]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"🔔 NEW WITHDRAWAL #{w_id}\n👤 User ID: {user.id}\n💰 Amount: {amount:.2f} POL\n💳 Method: {method}\n📱 Address: {account}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=get_markup())
    return ConversationHandler.END

# =========================
# ADMIN CONTROLS
# =========================

async def admin_action_callback(update, context):
    query = update.callback_query
    data = query.data
    
    if query.from_user.id != ADMIN_ID:
        await query.answer("You are not an admin!", show_alert=True)
        return

    action, req_id_str = data.split("_")[0], data.split("_")[1]
    req_id = int(req_id_str)
    
    req = await withdrawals_col.find_one({"req_id": req_id})
    if not req or req["status"] != "pending":
        await query.answer("Request already processed or not found.", show_alert=True)
        return

    if action == "approve":
        await withdrawals_col.update_one({"req_id": req_id}, {"$set": {"status": "approved"}})
        await query.edit_message_text(f"{query.message.text}\n\n✅ **APPROVED**")
        try:
            await context.bot.send_message(chat_id=req["user_id"], text=f"🎉 Your withdrawal of {req['amount']:.2f} POL has been APPROVED and sent!")
        except:
            pass

    elif action == "reject":
        await withdrawals_col.update_one({"req_id": req_id}, {"$set": {"status": "rejected"}})
        await add_balance(req["user_id"], req["amount"]) # Refund
        await query.edit_message_text(f"{query.message.text}\n\n❌ **REJECTED (Refunded)**")
        try:
            await context.bot.send_message(chat_id=req["user_id"], text=f"❌ Your withdrawal of {req['amount']:.2f} POL was REJECTED. The amount has been refunded to your balance.")
        except:
            pass

async def admin_broadcast(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    users = await users_col.find({}).to_list(length=None)
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=f"📢 BROADCAST:\n\n{message}")
            sent += 1
            await asyncio.sleep(0.05) # Prevent flood wait
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")

# =========================
# ROUTER & MAIN
# =========================

async def handle_menu_buttons(update, context):
    text = update.message.text
    if text == get_setting("button_earn"): await earn_tasks(update, context)
    elif text == get_setting("button_referral"): await refer_earn(update, context)
    elif text == get_setting("button_daily"): await daily_bonus(update, context)
    elif text == get_setting("button_balance"): await my_balance(update, context)
    elif text == get_setting("button_help"): await help_menu(update, context)
    elif text == get_setting("button_withdraw"):
        await update.message.reply_text("To withdraw, please click the Withdraw button again to start securely.")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def web_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

async def post_init(application):
    await load_settings()
    await create_default_task()

def main():
    threading.Thread(target=web_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    withdraw_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^" + re.escape(DEFAULT_SETTINGS["button_withdraw"]) + "$"), withdraw_start)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_method)],
            ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_account)],
        },
        fallbacks=[CommandHandler("cancel", withdraw_cancel)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(withdraw_conv)
    app.add_handler(CallbackQueryHandler(task_callback, pattern="^check_task_"))
    app.add_handler(CallbackQueryHandler(admin_action_callback, pattern="^(approve|reject)_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))

    print("Bot started successfully!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
            
