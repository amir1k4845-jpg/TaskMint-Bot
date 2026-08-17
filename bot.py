import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 Welcome to TaskMint Bot!\n\n"
        "💰 এখানে Tasks করে Points earn করতে পারবে।\n\n"
        "🚀 খুব শিগগিরই Tasks, Referral, Withdraw এবং Ads যোগ করা হবে!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ TaskMint Bot Help\n\n"
        "/start - Bot শুরু করুন\n"
        "/help - Help দেখুন"
    )


def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN is not set")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    print("TaskMint Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
