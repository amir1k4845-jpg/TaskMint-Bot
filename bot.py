import os
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
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

CHANNEL_USERNAME = "@Amir10m300"
TASK_REWARD = 10


# =========================
# Render Health Server
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TaskMint Bot is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# =========================
# User Points
# =========================

user_points = {}


# =========================
# Main Menu
# =========================

keyboard = [
    ["💰 Earn Tasks", "👥 Refer & Earn"],
    ["💳 Withdraw", "🎁 Daily Bonus"],
    ["📊 My Balance", "ℹ️ Help"],
]

reply_markup = ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True
)


# =========================
# Start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in user_points:
        user_points[user_id] = 0

    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 Tasks করে Points earn করতে পারবে।\n"
        "👥 বন্ধুদের Refer করে Points earn করতে পারবে।\n"
        "🎁 Daily Bonus পেতে পারবে।\n\n"
        "নিচের Menu থেকে একটি option বেছে নাও 👇",
        reply_markup=reply_markup
    )


# =========================
# Help
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন"
    )


# =========================
# Earn Tasks
# =========================

async def earn_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Join Channel",
                url="https://t.me/Amir10m300"
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
        "📢 Join our Telegram Channel\n\n"
        f"💰 Reward: +{TASK_REWARD} Points\n\n"
        "1️⃣ Join Channel button চাপো\n"
        "2️⃣ Channel-এ Join করো\n"
        "3️⃣ তারপর ✅ Check Task চাপো",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# Task Callback
# =========================

async def task_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # =====================
    # Check Channel Join
    # =====================

    if query.data == "check_join":

        try:

            member = await context.bot.get_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=user_id
            )

            status = member.status

            # User joined
            if status in ["member", "administrator", "creator"]:

                completed_tasks = context.user_data.setdefault(
                    "completed_tasks",
                    set()
                )

                # Already rewarded
                if "join_task" in completed_tasks:

                    await query.edit_message_text(
                        "⚠️ এই Task তুমি আগেই complete করেছো!\n\n"
                        f"💰 Current Points: "
                        f"{user_points.get(user_id, 0)}"
                    )

                    return

                # Give points
                user_points[user_id] = (
                    user_points.get(user_id, 0)
                    + TASK_REWARD
                )

                completed_tasks.add("join_task")

                await query.edit_message_text(
                    "🎉 Task Completed!\n\n"
                    f"✅ +{TASK_REWARD} Points Added!\n\n"
                    f"💰 Total Points: "
                    f"{user_points[user_id]}"
                )

            else:

                await query.edit_message_text(
                    "❌ তুমি এখনো Channel-এ Join করোনি!\n\n"
                    "প্রথমে Channel-এ Join করো।\n"
                    "তারপর আবার ✅ Check Task চাপো।",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(
                                "📢 Join Channel",
                                url="https://t.me/Amir10m300"
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

            print("Channel check error:", e)

            await query.edit_message_text(
                "⚠️ Channel verification করা যাচ্ছে না।\n\n"
                "দয়া করে নিশ্চিত করো যে TaskMint Bot-কে "
                "Channel-এর Admin করা হয়েছে।"
            )

    # =====================
    # Back
    # =====================

    elif query.data == "back_menu":

        await query.message.delete()

        await query.message.reply_text(
            "🏠 Main Menu\n\n"
            "নিচের Menu থেকে একটি option বেছে নাও 👇",
            reply_markup=reply_markup
        )


# =========================
# Button Handler
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text

    # Earn Tasks
    if text == "💰 Earn Tasks":

        await earn_tasks(update, context)

    # Referral
    elif text == "👥 Refer & Earn":

        user_id = update.effective_user.id

        await update.message.reply_text(
            "👥 Refer & Earn\n\n"
            "তোমার Referral Link:\n"
            f"https://t.me/TaskMintBot?start={user_id}\n\n"
            "Referral system শীঘ্রই চালু হবে।"
        )

    # Withdraw
    elif text == "💳 Withdraw":

        points = user_points.get(
            update.effective_user.id,
            0
        )

        await update.message.reply_text(
            "💳 Withdraw\n\n"
            f"💰 Your Points: {points}\n\n"
            "Withdrawal system শীঘ্রই চালু হবে।"
        )

    # Daily Bonus
    elif text == "🎁 Daily Bonus":

        await update.message.reply_text(
            "🎁 Daily Bonus\n\n"
            "Daily Bonus system শীঘ্রই চালু হবে।"
        )

    # Balance
    elif text == "📊 My Balance":

        points = user_points.get(
            update.effective_user.id,
            0
        )

        await update.message.reply_text(
            "📊 My Balance\n\n"
            f"💰 Points: {points}\n"
            "💵 Balance: $0.00"
        )

    # Help
    elif text == "ℹ️ Help":

        await help_command(update, context)


# =========================
# Main
# =========================

def main():

    if not TOKEN:
        raise ValueError(
            "BOT_TOKEN is not set. "
            "Add BOT_TOKEN in Render Environment Variables."
        )

    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CallbackQueryHandler(task_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            button_handler
        )
    )

    print("TaskMint Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
