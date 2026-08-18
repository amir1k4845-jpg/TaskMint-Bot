import os
‎import threading
‎from http.server import BaseHTTPRequestHandler, HTTPServer
‎
‎from telegram import Update, ReplyKeyboardMarkup
‎from telegram.ext import (
‎    ApplicationBuilder,
‎    CommandHandler,
‎    ContextTypes,
‎    MessageHandler,
‎    filters,
‎)
‎
‎TOKEN = os.getenv("BOT_TOKEN")
‎PORT = int(os.getenv("PORT", "10000"))
‎
‎
‎# Render health server
‎class HealthHandler(BaseHTTPRequestHandler):
‎    def do_GET(self):
‎        self.send_response(200)
‎        self.end_headers()
‎        self.wfile.write(b"TaskMint Bot is running!")
‎
‎    def log_message(self, format, *args):
‎        pass
‎
‎
‎def start_web_server():
‎    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
‎    server.serve_forever()
‎
‎
‎# Main Menu
‎keyboard = [
‎    ["💰 Earn Tasks", "👥 Refer & Earn"],
‎    ["💳 Withdraw", "🎁 Daily Bonus"],
‎    ["📊 My Balance", "ℹ️ Help"],
‎]
‎
‎reply_markup = ReplyKeyboardMarkup(
‎    keyboard,
‎    resize_keyboard=True
‎)
‎
‎
‎async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    await update.message.reply_text(
‎        "🎉 Welcome to TaskMint Bot!\n\n"
‎        "💰 Tasks করে Points earn করতে পারবে।\n\n"
‎        "নিচের Menu থেকে একটি option বেছে নাও 👇",
‎        reply_markup=reply_markup
‎    )
‎
‎
‎async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    await update.message.reply_text(
‎        "ℹ️ TaskMint Bot Help\n\n"
‎        "/start - Bot শুরু করুন\n"
‎        "/help - Help দেখুন"
‎    )
‎
‎
‎async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
‎    text = update.message.text
‎
‎    if text == "💰 Earn Tasks":
‎        await update.message.reply_text(
‎            "💰 Earn Tasks\n\n"
‎            "শীঘ্রই নতুন Tasks যোগ করা হবে!"
‎        )
‎
‎    elif text == "👥 Refer & Earn":
‎        await update.message.reply_text(
‎            "👥 Referral system শীঘ্রই চালু হবে!"
‎        )
‎
‎    elif text == "💳 Withdraw":
‎        await update.message.reply_text(
‎            "💳 Withdrawal system শীঘ্রই চালু হবে!"
‎        )
‎
‎    elif text == "🎁 Daily Bonus":
‎        await update.message.reply_text(
‎            "🎁 Daily Bonus শীঘ্রই চালু হবে!"
‎        )
‎
‎    elif text == "📊 My Balance":
‎        await update.message.reply_text(
‎            "📊 Your Balance: 0 Points"
‎        )
‎
‎    elif text == "ℹ️ Help":
‎        await help_command(update, context)
‎
‎
‎def main():
‎    if not TOKEN:
‎        raise ValueError("BOT_TOKEN is not set")
‎
‎    # Start Render health server
‎    threading.Thread(
‎        target=start_web_server,
‎        daemon=True
‎    ).start()
‎
‎    app = ApplicationBuilder().token(TOKEN).build()
‎
‎    app.add_handler(CommandHandler("start", start))
‎    app.add_handler(CommandHandler("help", help_command))
‎
‎    app.add_handler(
‎        MessageHandler(
‎            filters.TEXT & ~filters.COMMAND,
‎            button_handler
‎        )
‎    )
‎
‎    print("TaskMint Bot is running...")
‎    app.run_polling()
‎
‎
‎if __name__ == "__main__":
‎    main()
‎
‎
