import os
import re
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageService, MessageMediaWebPage, MessageMediaUnsupported
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from aiohttp import web

# ---------------- LOAD ENV ----------------
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
TARGET_CHANNEL = int(os.getenv("TARGET_CHANNEL"))
PORT = int(os.getenv("PORT", 8000))

# ---------------- REGEX ----------------
INVITE_REGEX = r"https://t\.me/(?:\+|joinchat/)([a-zA-Z0-9_-]+)"
MESSAGE_REGEX = r"https://t\.me/(?:c/)?([\w\d_]+)/(\d+)"

# ---------------- USERBOT ----------------
userbot = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)

# ---------------- TRACK ACTIVE TASKS ----------------
active_tasks = {}
task_data = {}

# ---------------- HEALTH CHECK SERVER ----------------
async def health_check(request):
    """Health check endpoint for Koyeb"""
    return web.Response(text="OK", status=200)

async def start_health_server():
    """Start health check server"""
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Health check server running on port {PORT}")

# ---------------- EXTRACT CHANNEL ID ----------------
async def get_channel_id(link: str) -> tuple:
    """Extract channel ID from invite link or message link"""
    
    # Check if it's an invite link
    invite_match = re.search(INVITE_REGEX, link)
    if invite_match:
        try:
            entity = await userbot.get_entity(link)
            return entity.id, entity.title
        except Exception as e:
            return None, f"Error: {e}"
    
    # Check if it's a message link
    msg_match = re.search(MESSAGE_REGEX, link)
    if msg_match:
        chat = msg_match.group(1)
        
        # Private channel (starts with c/)
        if chat.isdigit():
            chat_id = int("-100" + chat)
        else:
            chat_id = chat
        
        try:
            entity = await userbot.get_entity(chat_id)
            return entity.id, entity.title
        except Exception as e:
            return None, f"Error: {e}"
    
    return None, "Invalid link format"

# ---------------- COUNT TOTAL MEDIA ----------------
async def count_total_media(chat_id: int) -> int:
    """Count total media messages in channel"""
    try:
        total = 0
        async for message in userbot.iter_messages(chat_id, limit=1000):
            # Skip web pages and unsupported media
            if message.media and not message.noforwards:
                if not isinstance(message.media, (MessageMediaWebPage, MessageMediaUnsupported)):
                    total += 1
        return total
    except:
        return 0

# ---------------- COPY ALL MESSAGES ----------------
async def copy_all_messages(chat_id: int, chat_name: str, status_msg, user_id: int):
    """Copy all messages from message ID 1 onwards (unrestricted only)"""
    
    message_id = 1
    total_copied = 0
    total_skipped = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 100
    
    # Get total media count
    await status_msg.edit_text(
        f"🔍 Counting total media...",
        reply_markup=None
    )
    total_media = await count_total_media(chat_id)
    
    # Create stop button
    keyboard = [[InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await status_msg.edit_text(
        f"✅ **Channel Found!**\n"
        f"📢 {chat_name}\n"
        f"🆔 `{chat_id}`\n\n"
        f"🚀 Starting copy... {total_copied}/{total_media}",
        reply_markup=reply_markup
    )
    
    while True:
        # Check if task was cancelled
        if user_id in active_tasks and not active_tasks[user_id]:
            keyboard = [[InlineKeyboardButton("♻️ Restart", callback_data=f"restart_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await status_msg.edit_text(
                f"⛔ **Task Stopped**\n\n"
                f"📢 {chat_name}\n"
                f"✅ Copied: {total_copied}/{total_media}\n"
                f"⏭️ Skipped: {total_skipped}",
                reply_markup=reply_markup
            )
            break
        
        # Stop if too many consecutive failures
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            keyboard = [[InlineKeyboardButton("♻️ Restart", callback_data=f"restart_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await status_msg.edit_text(
                f"🏁 **Task Completed**\n\n"
                f"📢 {chat_name}\n"
                f"✅ Copied: {total_copied}/{total_media}\n"
                f"⏭️ Skipped: {total_skipped}\n"
                f"📍 Last checked: #{message_id - consecutive_failures}",
                reply_markup=reply_markup
            )
            break
        
        try:
            # Get message
            msg = await userbot.get_messages(chat_id, ids=message_id)
            
            # Check if message is None or is a service message
            if msg is None or isinstance(msg, MessageService):
                message_id += 1
                total_skipped += 1
                consecutive_failures += 1
                continue
            
            # Reset consecutive failures
            consecutive_failures = 0
            
            # Skip if no media
            if not msg.media:
                message_id += 1
                total_skipped += 1
                continue
            
            # Skip web pages and unsupported media types
            if isinstance(msg.media, (MessageMediaWebPage, MessageMediaUnsupported)):
                message_id += 1
                total_skipped += 1
                print(f"Skipped webpage/unsupported media #{message_id - 1}")
                continue
            
            # Skip if restricted
            if msg.noforwards:
                message_id += 1
                total_skipped += 1
                print(f"Skipped restricted message #{message_id - 1}")
                continue
            
            # ---------- SEND FILE WITHOUT FORWARD TAG ----------
            try:
                # Send file directly (removes forward tag automatically)
                await userbot.send_file(
                    TARGET_CHANNEL,
                    msg.media,
                    caption=""  # No caption
                )
                
                total_copied += 1
                print(f"✅ Copied message #{message_id}")
                
            except Exception as e:
                print(f"Send failed for #{message_id}: {e}")
                total_skipped += 1
            
            # Update status every 5 messages
            if message_id % 5 == 0:
                keyboard = [[InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{user_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await status_msg.edit_text(
                        f"✅ **Channel Found!**\n"
                        f"📢 {chat_name}\n"
                        f"🆔 `{chat_id}`\n\n"
                        f"🚀 Starting copy... {total_copied}/{total_media}\n"
                        f"📍 Current: #{message_id}",
                        reply_markup=reply_markup
                    )
                except:
                    pass
            
            # Telegram ToS: 5 second delay
            await asyncio.sleep(5)
            message_id += 1
            
        except FloodWaitError as e:
            keyboard = [[InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await status_msg.edit_text(
                f"⏳ **FloodWait Triggered**\n"
                f"Waiting {e.seconds} seconds...\n\n"
                f"Progress: {total_copied}/{total_media}",
                reply_markup=reply_markup
            )
            await asyncio.sleep(e.seconds)
            
        except Exception as e:
            print(f"Error at message #{message_id}: {e}")
            message_id += 1
            total_skipped += 1
            consecutive_failures += 1
    
    # Clean up task tracker
    if user_id in active_tasks:
        del active_tasks[user_id]

# ---------------- BUTTON CALLBACK HANDLER ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if user_id != OWNER_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    
    # Stop button
    if data.startswith("stop_"):
        if user_id in active_tasks:
            active_tasks[user_id] = False
            await query.answer("⏹️ Stopping...", show_alert=False)
    
    # Restart button
    elif data.startswith("restart_"):
        if user_id in task_data:
            chat_id, chat_name = task_data[user_id]
            active_tasks[user_id] = True
            
            await query.message.edit_text(
                f"♻️ **Restarting...**\n\n"
                f"📢 {chat_name}\n"
                f"🆔 `{chat_id}`"
            )
            
            # Start copying again
            asyncio.create_task(copy_all_messages(chat_id, chat_name, query.message, user_id))

# ---------------- BOT HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    await update.message.reply_text(
        "👋 **Telegram Bulk Copy Bot**\n\n"
        "📝 **How to use:**\n"
        "1. Add bot to target channel as admin\n"
        "2. Join the source channel\n"
        "3. Send me invite/message link\n"
        "4. Use Stop/Restart buttons\n\n"
        "⚡ **Features:**\n"
        "• No forwarded tag\n"
        "• No captions\n"
        "• Only unrestricted content\n"
        "• 5 second delay (Telegram ToS)\n"
        "• Skips web pages & text messages\n\n"
        "**Supported links:**\n"
        "• `https://t.me/+ABC123`\n"
        "• `https://t.me/channelname/123`\n"
        "• `https://t.me/c/1234567890/123`"
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    user_id = update.effective_user.id
    
    # Check if already running
    if user_id in active_tasks and active_tasks[user_id]:
        await update.message.reply_text(
            "⚠️ A task is already running!\n"
            "Use the Stop button first."
        )
        return
    
    link = update.message.text.strip()
    
    # Extract channel ID
    processing = await update.message.reply_text("🔍 Extracting channel info...")
    chat_id, result = await get_channel_id(link)
    
    if not chat_id:
        await processing.edit_text(f"❌ {result}")
        return
    
    # Store task data for restart
    task_data[user_id] = (chat_id, result)
    
    # Mark task as active
    active_tasks[user_id] = True
    
    # Start copying
    asyncio.create_task(copy_all_messages(chat_id, result, processing, user_id))

# ---------------- START USERBOT ----------------
async def start_userbot():
    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ UserBot: {me.first_name} (@{me.username or 'no username'})")

# ---------------- MAIN ----------------
async def main():
    # Start health check server
    await start_health_server()
    
    # Start userbot
    await start_userbot()
    
    # Build telegram bot
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("✅ Bot started - Ready to copy!")
    
    # Start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
