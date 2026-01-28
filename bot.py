import os
import re
import asyncio
import json
from datetime import datetime
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

# ==================== ENVIRONMENT VARIABLES ====================
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
TARGET_CHANNEL = int(os.getenv("TARGET_CHANNEL"))
PORT = int(os.getenv("PORT", 8000))
DATABASE_FILE = "monitoring_channels.json"

# ==================== REGEX PATTERNS ====================
MESSAGE_REGEX = r"https://t\.me/(?:c/)?([\w\d_]+)/(\d+)"
INVITE_REGEX = r"https://t\.me/(?:\+|joinchat/)([a-zA-Z0-9_-]+)"

# ==================== USERBOT ====================
userbot = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)

# ==================== GLOBAL STATE ====================
monitoring_channels = {}  # {chat_id: {name, last_msg_id, task}}
user_sessions = {}  # Track user's current operation

# ==================== DATABASE FUNCTIONS ====================
def load_monitoring_db():
    """Load monitored channels from JSON"""
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_monitoring_db(data):
    """Save monitored channels to JSON"""
    with open(DATABASE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def add_monitoring_channel(chat_id, chat_name, last_msg_id):
    """Add channel to monitoring database"""
    db = load_monitoring_db()
    db[str(chat_id)] = {
        "name": chat_name,
        "added_at": datetime.now().isoformat(),
        "last_msg_id": last_msg_id
    }
    save_monitoring_db(db)

def remove_monitoring_channel(chat_id):
    """Remove channel from monitoring database"""
    db = load_monitoring_db()
    if str(chat_id) in db:
        del db[str(chat_id)]
        save_monitoring_db(db)

# ==================== HEALTH CHECK SERVER ====================
async def health_check(request):
    """Health check endpoint"""
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

# ==================== EXTRACT MESSAGE IDS FROM LINKS ====================
async def get_message_ids(link: str) -> tuple:
    """Extract chat_id and message_id from link"""
    msg_match = re.search(MESSAGE_REGEX, link)
    if msg_match:
        chat = msg_match.group(1)
        msg_id = int(msg_match.group(2))
        
        # Private channel (starts with c/)
        if chat.isdigit():
            chat_id = int("-100" + chat)
        else:
            chat_id = chat
        
        try:
            entity = await userbot.get_entity(chat_id)
            return entity.id, entity.title, msg_id
        except Exception as e:
            return None, f"Error: {e}", None
    
    return None, "Invalid link format", None

# ==================== FORWARD MEDIA IN RANGE ====================
async def forward_media_range(chat_id: int, chat_name: str, start_id: int, end_id: int, status_msg):
    """Forward media from start_id to end_id"""
    
    if start_id > end_id:
        start_id, end_id = end_id, start_id
    
    total_forwarded = 0
    total_skipped = 0
    message_id = start_id
    
    # Count total media first
    await status_msg.edit_text(
        f"🔍 Counting media in range...\n"
        f"From: #{start_id} to #{end_id}",
        reply_markup=None
    )
    
    total_media = 0
    for msg_id_check in range(start_id, end_id + 1):
        try:
            msg = await userbot.get_messages(chat_id, ids=msg_id_check)
            if msg and msg.media and not msg.noforwards:
                if not isinstance(msg.media, (MessageMediaWebPage, MessageMediaUnsupported)):
                    if msg.photo or msg.document or msg.video:
                        total_media += 1
        except:
            pass
    
    # Start forwarding
    await status_msg.edit_text(
        f"📤 **Forwarding Media**\n"
        f"📢 {chat_name}\n"
        f"🆔 `{chat_id}`\n\n"
        f"📊 Total Media Found: {total_media}\n"
        f"📍 Range: #{start_id} → #{end_id}\n"
        f"🚀 Progress: {total_forwarded}/{total_media}"
    )
    
    while message_id <= end_id:
        try:
            msg = await userbot.get_messages(chat_id, ids=message_id)
            
            if msg is None or isinstance(msg, MessageService):
                message_id += 1
                total_skipped += 1
                continue
            
            if not msg.media:
                message_id += 1
                total_skipped += 1
                continue
            
            if isinstance(msg.media, (MessageMediaWebPage, MessageMediaUnsupported)):
                message_id += 1
                total_skipped += 1
                continue
            
            if msg.noforwards:
                message_id += 1
                total_skipped += 1
                continue
            
            # Forward only photo, document, video
            if msg.photo or msg.document or msg.video:
                try:
                    await userbot.send_file(
                        TARGET_CHANNEL,
                        msg.media,
                        caption=""
                    )
                    total_forwarded += 1
                    print(f"✅ Forwarded #{message_id}")
                except Exception as e:
                    print(f"⚠️ Send failed #{message_id}: {e}")
                    total_skipped += 1
            else:
                total_skipped += 1
            
            # Update status every 5 messages
            if message_id % 5 == 0:
                try:
                    await status_msg.edit_text(
                        f"📤 **Forwarding...**\n"
                        f"📢 {chat_name}\n"
                        f"🚀 Progress: {total_forwarded}/{total_media}\n"
                        f"📍 Current: #{message_id}/{end_id}\n"
                        f"⏭️ Skipped: {total_skipped}"
                    )
                except:
                    pass
            
            # Telegram ToS delay
            await asyncio.sleep(5)
            message_id += 1
            
        except FloodWaitError as e:
            await status_msg.edit_text(
                f"⏳ **FloodWait Triggered**\n"
                f"⏰ Waiting {e.seconds} seconds...\n\n"
                f"Progress: {total_forwarded}/{total_media}"
            )
            await asyncio.sleep(e.seconds)
            
        except Exception as e:
            print(f"❌ Error at message #{message_id}: {e}")
            message_id += 1
            total_skipped += 1
    
    # Final status
    await status_msg.edit_text(
        f"✅ **Forwarding Complete!**\n\n"
        f"📢 {chat_name}\n"
        f"✅ Forwarded: {total_forwarded}/{total_media}\n"
        f"⏭️ Skipped: {total_skipped}\n"
        f"📍 Range: #{start_id} → #{end_id}"
    )
    
    return chat_id, chat_name, end_id

# ==================== MONITOR CHANNEL FOR NEW MEDIA ====================
async def monitor_channel_for_new_media(chat_id: int, chat_name: str, last_msg_id: int):
    """Monitor channel for new media and auto-forward"""
    
    print(f"\n{'='*60}")
    print(f"🔔 MONITORING STARTED: {chat_name} (ID: {chat_id})")
    print(f"{'='*60}\n")
    
    current_last_id = last_msg_id
    
    while True:
        try:
            # Check if monitoring was stopped
            if chat_id not in monitoring_channels:
                print(f"❌ MONITORING STOPPED: {chat_name}\n")
                break
            
            # Get latest message
            try:
                messages = await userbot.get_messages(chat_id, limit=1)
                if messages:
                    latest_msg_id = messages[0].id
                    
                    # New messages found
                    if latest_msg_id > current_last_id:
                        new_count = 0
                        
                        # Check each new message
                        for msg_id in range(current_last_id + 1, latest_msg_id + 1):
                            if chat_id not in monitoring_channels:
                                break
                            
                            try:
                                msg = await userbot.get_messages(chat_id, ids=msg_id)
                                
                                # Valid media message?
                                if msg and msg.media and not msg.noforwards:
                                    if not isinstance(msg.media, (MessageMediaWebPage, MessageMediaUnsupported)):
                                        if msg.photo or msg.document or msg.video:
                                            
                                            # Forward it
                                            await userbot.send_file(
                                                TARGET_CHANNEL,
                                                msg.media,
                                                caption=""
                                            )
                                            
                                            media_type = '📷' if msg.photo else '📄' if msg.document else '🎬'
                                            print(f"🚀 NEW MEDIA FORWARDED! #{msg_id} {media_type} from {chat_name}")
                                            
                                            new_count += 1
                                            
                                            # Small delay between new media
                                            await asyncio.sleep(2)
                                            
                            except FloodWaitError as e:
                                print(f"⏳ FloodWait! Waiting {e.seconds}s...")
                                await asyncio.sleep(e.seconds)
                            except Exception as e:
                                print(f"❌ Failed to forward #{msg_id}: {e}")
                        
                        current_last_id = latest_msg_id
                        
                        if new_count > 0:
                            print(f"✅ Total new media forwarded: {new_count}\n")
            
            except Exception as e:
                print(f"❌ Error checking {chat_name}: {e}")
            
            # Check every 10 seconds
            await asyncio.sleep(10)
            
        except Exception as e:
            print(f"❌ Monitor error for {chat_name}: {e}")
            await asyncio.sleep(10)

# ==================== BOT HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    await update.message.reply_text(
        "👋 **Telegram Media Forwarder**\n\n"
        "📝 **How to use:**\n"
        "1. Send me source channel link (message link)\n"
        "2. Send me start message link\n"
        "3. Send me end message link\n"
        "4. Bot forwards all media in range\n"
        "5. Bot auto-monitors new media\n\n"
        "📋 **Commands:**\n"
        "/channels - View monitored channels\n"
        "/start - This help message\n\n"
        "**Supported links:**\n"
        "`https://t.me/channelname/123`\n"
        "`https://t.me/c/1234567890/456`"
    )

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all monitored channels"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    db = load_monitoring_db()
    
    if not db:
        await update.message.reply_text("❌ No channels being monitored")
        return
    
    text = "📊 **Currently Monitoring:**\n\n"
    
    for chat_id, data in db.items():
        chat_id_int = int(chat_id)
        status = "🟢" if chat_id_int in monitoring_channels else "⚪"
        
        text += f"{status} **{data['name']}**\n"
        text += f"   🆔 `{chat_id}`\n"
        text += f"   📍 Last ID: {data['last_msg_id']}\n"
        text += f"   📅 Added: {data['added_at'][:10]}\n\n"
    
    await update.message.reply_text(text)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input - expecting message links"""
    if update.effective_user.id != OWNER_ID:
        return
    
    user_id = update.effective_user.id
    link = update.message.text.strip()
    
    # Initialize user session if needed
    if user_id not in user_sessions:
        user_sessions[user_id] = {"step": 0}
    
    session = user_sessions[user_id]
    
    # STEP 1: Get source channel link
    if session["step"] == 0:
        processing = await update.message.reply_text("🔍 Extracting source channel...")
        chat_id, result, msg_id = await get_message_ids(link)
        
        if not chat_id:
            await processing.edit_text(f"❌ Invalid link. Please send message link from channel")
            return
        
        session["source_chat_id"] = chat_id
        session["source_chat_name"] = result
        session["step"] = 1
        
        await processing.edit_text(
            f"✅ Source channel found: {result}\n\n"
            f"Now send the **START** message link"
        )
    
    # STEP 2: Get start message ID
    elif session["step"] == 1:
        chat_id, result, start_msg_id = await get_message_ids(link)
        
        if not start_msg_id:
            await update.message.reply_text("❌ Could not extract message ID from link")
            return
        
        session["start_msg_id"] = start_msg_id
        session["step"] = 2
        
        await update.message.reply_text(
            f"✅ Start message set: #{start_msg_id}\n\n"
            f"Now send the **END** message link"
        )
    
    # STEP 3: Get end message ID and start forwarding
    elif session["step"] == 2:
        chat_id, result, end_msg_id = await get_message_ids(link)
        
        if not end_msg_id:
            await update.message.reply_text("❌ Could not extract message ID from link")
            return
        
        # Get source info
        source_chat_id = session["source_chat_id"]
        source_chat_name = session["source_chat_name"]
        start_msg_id = session["start_msg_id"]
        
        # Start forwarding
        status_msg = await update.message.reply_text(
            f"⏳ **Starting forwarding...**\n\n"
            f"📢 {source_chat_name}\n"
            f"📍 #{start_msg_id} → #{end_msg_id}"
        )
        
        # Forward the media
        final_chat_id, final_chat_name, final_last_id = await forward_media_range(
            source_chat_id, 
            source_chat_name, 
            start_msg_id, 
            end_msg_id, 
            status_msg
        )
        
        # Add to monitoring
        add_monitoring_channel(final_chat_id, final_chat_name, final_last_id)
        
        # Start monitoring for new media
        if final_chat_id not in monitoring_channels:
            monitoring_channels[final_chat_id] = {
                "name": final_chat_name,
                "last_msg_id": final_last_id,
                "task": None
            }
            
            # Create monitoring task
            task = asyncio.create_task(
                monitor_channel_for_new_media(final_chat_id, final_chat_name, final_last_id)
            )
            monitoring_channels[final_chat_id]["task"] = task
            
            await status_msg.edit_text(
                f"✅ **Forwarding Complete!**\n\n"
                f"📢 {final_chat_name}\n"
                f"📍 Range: #{start_msg_id} → #{end_msg_id}\n\n"
                f"🔔 Now monitoring for new media..."
            )
        
        # Reset user session
        user_sessions[user_id] = {"step": 0}

# ==================== START USERBOT ====================
async def start_userbot():
    """Start Telethon userbot"""
    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ UserBot: {me.first_name} (@{me.username or 'no username'})")

# ==================== RESTORE MONITORING ON STARTUP ====================
async def restore_monitoring():
    """Restore monitoring channels on bot startup"""
    db = load_monitoring_db()
    
    for chat_id, data in db.items():
        chat_id_int = int(chat_id)
        
        if chat_id_int not in monitoring_channels:
            monitoring_channels[chat_id_int] = {
                "name": data["name"],
                "last_msg_id": data["last_msg_id"],
                "task": None
            }
            
            # Restore monitoring task
            task = asyncio.create_task(
                monitor_channel_for_new_media(chat_id_int, data["name"], data["last_msg_id"])
            )
            monitoring_channels[chat_id_int]["task"] = task
            
            print(f"✅ Restored monitoring: {data['name']}")

# ==================== MAIN ====================
async def main():
    # Start health check
    await start_health_server()
    
    # Start userbot
    await start_userbot()
    
    # Restore monitoring channels
    await restore_monitoring()
    
    # Build bot
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    print("✅ Bot started - Ready to forward!")
    
    # Start bot
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
