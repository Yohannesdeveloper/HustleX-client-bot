# bot/main.py
# States for Telegram Job Posting
JOB_TITLE, JOB_TYPE, WORK_LOCATION, SALARY, DEADLINE, DESCRIPTION, CLIENT_TYPE, JOB_LINK, COMPANY_NAME, VERIFIED, PREVIOUS_JOBS = range(11)
import os
import re
import logging
import json
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand, MenuButtonCommands
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
from telegram.error import TelegramError
from urllib.parse import urlparse
import aiohttp
import io
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables from .env
load_dotenv()
TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://hustlexet.vercel.app/")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://yohannesfk123:CKNujByIaepiwyGf@cluster0.mrtm8aj.mongodb.net/hustlex?retryWrites=true&w=majority&appName=Cluster0")

# MongoDB connection setup
mongo_client = None
db = None
DB_NAME = "hustlex"
DEFAULT_JOB_ID = "6a31521bf3edf7daab32416c"
BOT_USERNAME = os.getenv("BOT_USERNAME", "HustleXet_bot")

def get_db():
    """Return MongoDB database handle."""
    global mongo_client, db
    try:
        if mongo_client is None:
            mongo_client = MongoClient(
                MONGODB_URI,
                maxPoolSize=50,
                minPoolSize=5,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                serverSelectionTimeoutMS=10000,
            )
            db = mongo_client[DB_NAME]
            logger.info("Successfully connected to MongoDB")
        return db
    except ConnectionFailure as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return None

def is_user_registered(user_id: int) -> bool:
    """Check if user completed registration in MongoDB."""
    database = get_db()
    if database is None:
        logger.warning(f"is_user_registered({user_id}): get_db() returned None")
        return False
    try:
        user = database.registered_users.find_one({"user_id": user_id})
        if user is not None:
            return True
        logger.info(f"is_user_registered({user_id}): not found in MongoDB")
        return False
    except Exception as e:
        logger.error(f"Error checking user registration for {user_id}: {e}")
        return False

async def check_registration_via_api(user_id: int) -> bool:
    """Fallback: check registration via the API (Vercel) when direct MongoDB check fails."""
    url = f"{WEBAPP_URL.rstrip('/')}/api/user/status?user_id={user_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("registered", False)
    except Exception as e:
        logger.error(f"API registration check failed for {user_id}: {e}")
    return False

def get_user_profile(user_id: int):
    database = get_db()
    if database is None:
        return None
    try:
        return database.profiles.find_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"Error loading profile for {user_id}: {e}")
        return None

def has_user_phone(user_id: int) -> bool:
    profile = get_user_profile(user_id)
    if not profile:
        return False
    return bool(profile.get("phone") or profile.get("phone_number"))

def save_user_phone(user_id: int, phone: str) -> bool:
    database = get_db()
    if database is None:
        return False
    try:
        database.profiles.update_one(
            {"user_id": user_id},
            {"$set": {"phone": phone, "phone_number": phone, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"Error saving phone for {user_id}: {e}")
        return False

def is_profile_setup_complete(user_id: int) -> bool:
    profile = get_user_profile(user_id)
    if not profile:
        return False
    return bool(profile.get("name") and profile.get("age") and profile.get("sex"))

def parse_job_id_from_start(args) -> Optional[str]:
    if not args:
        return None
    param = args[0]
    if param.startswith("job_"):
        return param[4:]
    if param.startswith("apply_"):
        return param[6:]
    return param if len(param) == 24 else None

def get_pending_job_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("pending_job_id") or DEFAULT_JOB_ID

def register_user(user_id: int, username: str = None, first_name: str = None) -> bool:
    """Register user in MongoDB."""
    database = get_db()
    if database is None:
        return False
    try:
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "registered_at": datetime.utcnow(),
        }
        database.registered_users.update_one(
            {"user_id": user_id},
            {"$setOnInsert": user_data},
            upsert=True,
        )
        logger.info(f"User {user_id} registered successfully")
        return True
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        return False

def get_user_cv(user_id: int):
    database = get_db()
    if database is None:
        return None
    try:
        profile = database.profiles.find_one(
            {"user_id": user_id},
            {"cv_file_data": 1, "cv_filename": 1, "cv_mime_type": 1}
        )
        return profile
    except Exception as e:
        logger.error(f"Error loading CV for {user_id}: {e}")
        return None

def save_profile_fields(user_id: int, fields: dict) -> bool:
    database = get_db()
    if database is None:
        return False
    try:
        database.profiles.update_one(
            {"user_id": user_id},
            {"$set": {**fields, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"Error saving profile for {user_id}: {e}")
        return False

def save_job_to_db(job_data: dict) -> str:
    from bson import ObjectId
    database = get_db()
    if database is None:
        return DEFAULT_JOB_ID
    job_id = str(ObjectId())
    doc = {
        "job_id": job_id,
        "job_title": job_data.get("job_title"),
        "job_type": job_data.get("job_type"),
        "work_location": job_data.get("work_location"),
        "salary": job_data.get("salary"),
        "deadline": job_data.get("deadline"),
        "description": job_data.get("description"),
        "client_type": job_data.get("client_type"),
        "company_name": job_data.get("company_name"),
        "verified": job_data.get("verified"),
        "previous_jobs": job_data.get("previous_jobs"),
        "job_link": job_data.get("job_link"),
        "created_at": datetime.utcnow(),
    }
    database.jobs.insert_one(doc)
    return job_id

async def post_profile_card_to_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Post a freelancer profile card to @HustleXeth when profile is completed."""
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003194542999")
    profile = get_user_profile(user_id)
    if not profile:
        logger.warning(f"No profile found for user {user_id}, skipping channel post")
        return False

    name = profile.get("name", "Not set")
    raw_age = profile.get("age", None)
    age = str(raw_age) if raw_age and int(raw_age) > 0 else "N/A"
    sex = profile.get("sex", "N/A")
    username = profile.get("username") or ""
    contact = f"@{username}" if username else "N/A"

    database = get_db()
    user_info = None
    if database:
        user_info = database.registered_users.find_one({"user_id": user_id})
    tg_username = ""
    if user_info:
        tg_username = user_info.get("username") or ""
    if tg_username and not username:
        contact = f"@{tg_username}"

    profile_card = (
        f"🆕 New Freelancer Profile!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {name}\n"
        f"🎂 Age: {age}\n"
        f"⚧ Gender: {sex}\n"
        f"📱 Contact: {contact}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"HustleX — Elite Freelancers Worldwide\n"
        f"@HustleXet_bot"
    )

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=profile_card,
        )
        logger.info(f"Profile card posted to channel for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to post profile card to channel: {e}")
        return False


def delete_user(user_id: int) -> bool:
    """Delete all user data from MongoDB and in-memory storage."""
    database = get_db()
    success = True
    try:
        if database is not None:
            database.registered_users.delete_one({"user_id": user_id})
            database.profiles.delete_one({"user_id": user_id})
        registered_users.discard(user_id)
        user_cvs.pop(user_id, None)
        user_languages.pop(user_id, None)
        user_profiles.pop(user_id, None)
        user_posts.pop(user_id, None)
        logger.info(f"User {user_id} deleted successfully")
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}")
        success = False
    return success

# Simple in-memory storage for CV data and user preferences (replace with database in production)
user_cvs = {}
user_languages = {}
user_profiles = {}
user_posts = {}  # Stores user posts: {user_id: [{"message_id": 123, "title": "Job Title", ...}]}
registered_users = set()

# Helper function to validate bot token
async def validate_bot_token(token: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        bot_info = await context.bot.get_me()
        logger.info(f"Bot token validated successfully: @{bot_info.username}")
        return True
    except TelegramError as e:
        logger.error(f"Invalid bot token: {e}")
        return False

# Helper function to check bot permissions in the channel
async def check_bot_permissions(context: ContextTypes.DEFAULT_TYPE, channel_id: str) -> bool:
    try:
        bot_member = await context.bot.get_chat_member(chat_id=channel_id, user_id=context.bot.id)
        if bot_member.status not in ["administrator", "creator"]:
            logger.warning(f"Bot is not an admin in channel {channel_id}")
            return False
        if not bot_member.can_send_messages:
            logger.warning(f"Bot lacks 'Send Messages' permission in channel {channel_id}")
            return False
        logger.info(f"Bot has necessary permissions in channel {channel_id}")
        return True
    except TelegramError as e:
        logger.error(f"Error checking bot permissions in channel {channel_id}: {e}")
        return False

# Helper function to validate channel ID
async def validate_channel_id(context: ContextTypes.DEFAULT_TYPE, channel_id: str) -> bool:
    try:
        chat_info = await context.bot.get_chat(chat_id=channel_id)
        logger.info(f"Channel ID {channel_id} is valid: {chat_info.title}")
        return True
    except TelegramError as e:
        logger.error(f"Invalid channel ID {channel_id}: {e}")
        return False

# Helper function to validate WebApp URL
async def validate_webapp_url(url: str) -> bool:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    logger.info(f"WebApp URL {url} is reachable")
                    return True
                else:
                    logger.error(f"WebApp URL {url} returned status {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error accessing WebApp URL {url}: {e}")
            return False

# Enhanced Markdown escaping for Telegram MarkdownV2
def escape_markdown_v2(text: str) -> str:
    special_chars = r'([_\*\[\]\(\)~`>\#\+\-=\|\{\}\.\!])'
    return re.sub(special_chars, r'\\\1', str(text))

async def show_registration_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show registration WebApp for unregistered users."""
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    welcome_messages = {
        'en': {
            'welcome': (
                "👋 *Welcome to HustleX!* 🚀\n\n"
                "You stand at the gates of the **premier freelance kingdom** — where top 1% talent "
                "meets world-class opportunities. But first, you need your **welcome papers**.\n\n"
                "Registration takes **60 seconds** and unlocks:\n"
                "• 🎯 *Your Profile Throne* — Let clients discover your genius\n"
                "• 📋 *Application Command Center* — Track every conquest\n"
                "• ⚡ *Instant Apply* — One tap to your next big gig\n"
                "• 🌟 *Verified Status* — Flex on the competition\n\n"
                "This isn't just a sign-up — it's your **origin story**. 🦸\n\n"
                "👇 Tap below to begin your legend 👇"
            ),
            'register': "📝 Register Now — Join the Elite",
        },
    }
    messages = welcome_messages.get(lang_code, welcome_messages['en'])
    register_url = f"{WEBAPP_URL.rstrip('/')}/Register"
    keyboard = [[InlineKeyboardButton(messages['register'], web_app=WebAppInfo(url=register_url))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    target = update.effective_message or update.effective_chat
    if update.effective_message:
        await update.effective_message.reply_text(messages['welcome'], reply_markup=reply_markup)
    else:
        await update.effective_chat.send_message(messages['welcome'], reply_markup=reply_markup)

async def require_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    # Check in-memory set first (fastest, survives message interception)
    if user_id in registered_users:
        return True
    if is_user_registered(user_id):
        registered_users.add(user_id)
        return True
    if await check_registration_via_api(user_id):
        logger.info(f"User {user_id} confirmed registered via API fallback")
        registered_users.add(user_id)
        register_user(user_id, update.effective_user.username, update.effective_user.first_name)
        return True
    await show_registration_prompt(update, context)
    return False

async def prompt_phone_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    context.user_data['awaiting_phone'] = True
    phone_messages = {
        'en': "✅ Registration complete! 🎉\n\nPlease share your phone number so clients can reach you, or tap Cancel to skip.",
    }
    message = phone_messages.get(lang_code, phone_messages['en'])
    keyboard = [
        [KeyboardButton("📱 Share Phone Number", request_contact=True)],
        [KeyboardButton("❌ Cancel")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    if update.effective_message:
        await update.effective_message.reply_text(message, reply_markup=reply_markup)
    else:
        chat = update.effective_chat
        if chat:
            await chat.send_message(message, reply_markup=reply_markup)

async def prompt_profile_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    job_id = get_pending_job_id(context)
    profile_url = f"{WEBAPP_URL.rstrip('/')}/freelancer-profile-setup?job_id={job_id}"
    keyboard = [[InlineKeyboardButton("📝 Complete Profile Setup", web_app=WebAppInfo(url=profile_url))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    messages = {
        'en': "📝 Next step: complete your freelancer profile to start applying for jobs.",
    }
    message = messages.get(lang_code, messages['en'])
    chat = update.effective_chat
    if update.effective_message:
        await update.effective_message.reply_text(message, reply_markup=reply_markup)
    else:
        await chat.send_message(message, reply_markup=reply_markup)

async def send_job_details(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: str = None):
    job_id = job_id or get_pending_job_id(context)
    job_details_url = f"{WEBAPP_URL.rstrip('/')}/job-details/{job_id}"
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    messages = {
        'en': f"👋 Welcome! Here are the job details:\n\n{job_details_url}",
    }
    message = messages.get(lang_code, messages['en'])
    if update.effective_message:
        await update.effective_message.reply_text(message)
    else:
        await update.effective_chat.send_message(message)

async def route_registered_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route a registered user directly to menu or job details."""
    user_id = update.effective_user.id
    job_id = parse_job_id_from_start(context.args) or context.user_data.get("pending_job_id")
    if job_id:
        context.user_data["pending_job_id"] = job_id

    if job_id:
        await send_job_details(update, context, job_id)
    else:
        await menu_callback(update, context)

# ---------------------------
# /register_complete command
# ---------------------------
async def register_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark user as registered after completing registration on the website"""
    user_id = update.effective_user.id

    username = update.effective_user.username
    first_name = update.effective_user.first_name
    success = register_user(user_id, username, first_name)

    if not success:
        error_message = "❌ Failed to register. Please try again later."
        if update.effective_message:
            await update.effective_message.reply_text(error_message)
        else:
            await update.effective_chat.send_message(error_message)
        return

    registered_users.add(user_id)
    await post_registration_to_channel(context, user_id, username or "")
    await prompt_phone_share(update, context)

# ---------------------------
# /start command
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await validate_bot_token(TOKEN, context):
        if update.effective_chat:
            await update.effective_chat.send_message("❌ Error: Invalid bot token. Please contact the bot administrator.")
        logger.error("Cannot send message due to invalid bot token")
        return
    
    job_id = parse_job_id_from_start(context.args)
    if job_id:
        context.user_data["pending_job_id"] = job_id

    await route_registered_user(update, context)

# ---------------------------
# Utility function for safe message editing
# ---------------------------
async def safe_edit_message(query, text, reply_markup=None, parse_mode=None, context=None):
    """Safely edit a message, fallback to sending new message if edit fails"""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        # If editing fails, send a new message instead
        if context:
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
        )

# ---------------------------
# Menu callback
# ---------------------------
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update, context):
        return

    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific menu messages
    menu_messages = {
        'en': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nChoose a tab:",
            'profile': "Profile",
            'profile_desc': "Manage your freelancer profile and CV",
            'applications': "Applications",
            'applications_desc': "View and manage your job applications",
            'about': "About HustleX",
            'about_desc': "Learn more about HustleX platform",
            'settings': "Settings",
            'settings_desc': "Configure your preferences and account",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'es': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nElige una pestaña:",
            'profile': "Perfil",
            'profile_desc': "Gestiona tu perfil de freelancer y CV",
            'applications': "Aplicaciones",
            'applications_desc': "Ver y gestionar tus solicitudes de empleo",
            'about': "Acerca de HustleX",
            'about_desc': "Conoce más sobre la plataforma HustleX",
            'settings': "Configuración",
            'settings_desc': "Configura tus preferencias y cuenta",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'fr': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nChoisissez un onglet:",
            'profile': "Profil",
            'profile_desc': "Gérez votre profil de freelance et CV",
            'applications': "Candidatures",
            'applications_desc': "Voir et gérer vos candidatures",
            'about': "À propos de HustleX",
            'about_desc': "En savoir plus sur la plateforme HustleX",
            'settings': "Paramètres",
            'settings_desc': "Configurez vos préférences et compte",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'de': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nWählen Sie einen Tab:",
            'profile': "Profil",
            'profile_desc': "Verwalten Sie Ihr Freelancer-Profil und CV",
            'applications': "Bewerbungen",
            'applications_desc': "Bewerbungen anzeigen und verwalten",
            'about': "Über HustleX",
            'about_desc': "Erfahren Sie mehr über die HustleX-Plattform",
            'settings': "Einstellungen",
            'settings_desc': "Konfigurieren Sie Ihre Präferenzen und Konto",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'it': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nScegli una scheda:",
            'profile': "Profilo",
            'profile_desc': "Gestisci il tuo profilo freelance e CV",
            'applications': "Candidature",
            'applications_desc': "Visualizza e gestisci le tue candidature",
            'about': "Informazioni su HustleX",
            'about_desc': "Scopri di più sulla piattaforma HustleX",
            'settings': "Impostazioni",
            'settings_desc': "Configura le tue preferenze e account",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'pt': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nEscolha uma aba:",
            'profile': "Perfil",
            'profile_desc': "Gerencie seu perfil de freelancer e CV",
            'applications': "Candidaturas",
            'applications_desc': "Visualize e gerencie suas candidaturas",
            'about': "Sobre o HustleX",
            'about_desc': "Saiba mais sobre a plataforma HustleX",
            'settings': "Configurações",
            'settings_desc': "Configure suas preferências e conta",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        },
        'am': {
            'title': "🌐 https://hustlexet.vercel.app/\n\nአንድ ትር ይምረጡ:",
            'profile': "መገለጫ",
            'profile_desc': "የእርስዎን ፍሪላንሰር መገለጫ እና CV ያስተዳድሩ",
            'applications': "ማመልከቻዎች",
            'applications_desc': "የስራ መጠየቅዎችን ይመልከቱ እና ያስተዳድሩ",
            'about': "ስለ HustleX",
            'about_desc': "ስለ HustleX መድረክ የበለጠ ይወቁ",
            'settings': "ቅንብሮች",
            'settings_desc': "የእርስዎን ምርጫዎች እና መለያ ያስተካክሉ",
            'footer': "HustleX (https://hustlexet.vercel.app/)\nHustleX — Hire Elite Freelancers Worldwide\nConnect with top 1% freelancers in web development, MERN stack, UI/UX design & AI services. The premium marketplace for excellence.",
        }
    }
    
    messages = menu_messages.get(lang_code, menu_messages['en'])
    
    # Build menu with attached keyboard
    keyboard = [
        [KeyboardButton(f"ℹ️ {messages['about']}"), KeyboardButton(f"👤 {messages['profile']}")],
        [KeyboardButton(f"📋 {messages['applications']}"), KeyboardButton(f"⚙️ {messages['settings']}")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Build menu message
    menu_text = f"{messages['title']}\n\n"
    menu_text += "🔥 *Welcome to the Arena, Champion!* 🔥\n\n"
    menu_text += "You're now in the *HustleX command center* — where freelancers become legends "\
                 "and clients find their secret weapons. Every tab is a tool. Every click is a power-up.\n\n"
    menu_text += "*⚔️ Your Arsenal:*\n"
    menu_text += f"📋 {messages['applications']} — Track your conquests, seal the deals\n"
    menu_text += f"👤 {messages['profile']} — Your digital throne, flex your empire\n"
    menu_text += f"⚙️ {messages['settings']} — Calibrate your battlefield\n"
    menu_text += f"ℹ️ {messages['about']} — Know the kingdom you're building in\n\n"
    menu_text += "Let's make moves. 🚀\n\n"
    menu_text += messages['footer']
    
    if update.effective_message:
        await update.effective_message.reply_text(menu_text, reply_markup=reply_markup)
    else:
        await update.effective_chat.send_message(menu_text, reply_markup=reply_markup)

# ---------------------------
# Contact handler for phone number sharing
# ---------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number contact sharing"""
    user_id = update.effective_user.id
    contact = update.message.contact

    if contact and contact.user_id == user_id:
        save_user_phone(user_id, contact.phone_number)
        context.user_data.pop("awaiting_phone", None)
        await update.message.reply_text(
            "✅ Phone number saved! 📱",
            reply_markup=ReplyKeyboardRemove(),
        )
        await prompt_profile_setup(update, context)
    elif contact:
        await update.message.reply_text("Please share your own phone number using the Share button.")

# ---------------------------
# Web app data handler
# ---------------------------
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data sent from web app"""
    if update.message.web_app_data:
        try:
            data = update.message.web_app_data.data
            parsed_data = json.loads(data)

            if parsed_data.get('action') == 'register_complete':
                # Handle registration completion from WebApp
                user_id = update.effective_user.id
                username = update.effective_user.username
                first_name = update.effective_user.first_name
                success = register_user(user_id, username, first_name)

                if success:
                    registered_users.add(user_id)
                    await post_registration_to_channel(context, user_id, username or "")
                    # Send phone sharing popup as a new message
                    lang_code = user_languages.get(user_id, 'en')
                    context.user_data['awaiting_phone'] = True
                    phone_messages = {
                        'en': "✅ Registration complete! 🎉\n\nPlease share your phone number so clients can reach you, or tap Cancel to skip.",
                    }
                    message = phone_messages.get(lang_code, phone_messages['en'])
                    keyboard = [
                        [KeyboardButton("📱 Share Phone Number", request_contact=True)],
                        [KeyboardButton("❌ Cancel")],
                    ]
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                    await update.effective_chat.send_message(message, reply_markup=reply_markup)
            elif parsed_data.get('action') == 'profile_complete':
                user_id = update.effective_user.id
                job_id = parsed_data.get('job_id') or get_pending_job_id(context)
                logger.info(f"Profile completed for user {user_id}")
                await send_job_details(update, context, job_id)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            pass

# ---------------------------
# Text message handler for menu
# ---------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    text = update.effective_message.text.strip()

    # Intercept API's "Registration Successful" message and register user in-memory
    if "Registration Successful" in text:
        registered_users.add(user_id)
        register_user(user_id, update.effective_user.username, update.effective_user.first_name)
        return

    if text == "❌ Cancel" and (
        context.user_data.get("awaiting_phone")
        or (is_user_registered(user_id) and not has_user_phone(user_id))
    ):
        context.user_data.pop("awaiting_phone", None)
        await update.effective_message.reply_text(
            "Phone sharing skipped.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await prompt_profile_setup(update, context)
        return
    
    # Language-specific menu texts (all possible options)
    menu_texts = {
        'Menu': 'menu',
        'Main Menu': 'menu',
        'Menú': 'menu',
        'ሜኑ': 'menu',
        # Start menu
        'Profile': 'profile',
        '👤 Profile': 'profile',
        'Perfil': 'profile',
        '👤 Perfil': 'profile',
        'Profil': 'profile',
        '👤 Profil': 'profile',
        'መገለጫ': 'profile',
        '👤 መገለጫ': 'profile',
        'Applications': 'applications',
        '📋 Applications': 'applications',
        'Aplicaciones': 'applications',
        '📋 Aplicaciones': 'applications',
        'Candidatures': 'applications',
        '📋 Candidatures': 'applications',
        'Bewerbungen': 'applications',
        '📋 Bewerbungen': 'applications',
        'Candidature': 'applications',
        '📋 Candidature': 'applications',
        'ማመልከቻዎች': 'applications',
        '📋 ማመልከቻዎች': 'applications',
        'About HustleX': 'about',
        'ℹ️ About HustleX': 'about',
        'Acerca de HustleX': 'about',
        'ℹ️ Acerca de HustleX': 'about',
        'À propos de HustleX': 'about',
        'ℹ️ À propos de HustleX': 'about',
        'Über HustleX': 'about',
        'ℹ️ Über HustleX': 'about',
        'Informazioni su HustleX': 'about',
        'ℹ️ Informazioni su HustleX': 'about',
        'Sobre o HustleX': 'about',
        'ℹ️ Sobre o HustleX': 'about',
        'ስለ HustleX': 'about',
        'ℹ️ ስለ HustleX': 'about',
        'Settings': 'settings',
        '⚙️ Settings': 'settings',
        'Configuración': 'settings',
        '⚙️ Configuración': 'settings',
        'Paramètres': 'settings',
        '⚙️ Paramètres': 'settings',
        'Einstellungen': 'settings',
        '⚙️ Einstellungen': 'settings',
        'Impostazioni': 'settings',
        '⚙️ Impostazioni': 'settings',
        'Configurações': 'settings',
        '⚙️ Configurações': 'settings',
        'ቅንብሮች': 'settings',
        '⚙️ ቅንብሮች': 'settings',
        # Back buttons
        '⬅️ Back to Menu': 'menu',
        '⬅️ Volver al Menú': 'menu',
        '⬅️ Retour au Menu': 'menu',
        '⬅️ Zurück zum Menü': 'menu',
        '⬅️ Torna al Menu': 'menu',
        '⬅️ Voltar ao Menu': 'menu',
        '⬅️ ወደ ሜኑ ይመለሱ': 'menu',
        # Settings sub-menus
        '🌍 Languages': 'settings_languages',
        '🌍 Idiomas': 'settings_languages',
        '🌍 Langues': 'settings_languages',
        '🌍 Sprachen': 'settings_languages',
        '🌍 Lingue': 'settings_languages',
        '🌍 ቋንቋዎች': 'settings_languages',
        '👤 Account': 'settings_account',
        '👤 Cuenta': 'settings_account',
        '👤 Compte': 'settings_account',
        '👤 Konto': 'settings_account',
        '👤 Conta': 'settings_account',
        '👤 መለያ': 'settings_account',
        '📄 My CV': 'settings_cv',
        '📄 Mi CV': 'settings_cv',
        '📄 Mon CV': 'settings_cv',
        '📄 Mein Lebenslauf': 'settings_cv',
        '📄 Il Mio CV': 'settings_cv',
        '📄 Meu CV': 'settings_cv',
        '📄 የእኔ CV': 'settings_cv',
        '📋 Terms and Conditions': 'settings_terms',
        '📋 Términos y Condiciones': 'settings_terms',
        '📋 Termes et Conditions': 'settings_terms',
        '📋 Geschäftsbedingungen': 'settings_terms',
        '📋 Termini e Condizioni': 'settings_terms',
        '📋 Termos e Condições': 'settings_terms',
        '📋 ውሎች እና ሁኔታዎች': 'settings_terms',
        # Language selection options
        '🇺🇸 English': 'lang_en',
        '🇪🇸 Español': 'lang_es',
        '🇫🇷 Français': 'lang_fr',
        '🇩🇪 Deutsch': 'lang_de',
        '🇮🇹 Italiano': 'lang_it',
        '🇵🇹 Português': 'lang_pt',
        '🇪🇹 አማርኛ (Amharic)': 'lang_am',
        # Back to settings
        '⬅️ Back to Settings': 'settings',
        '⬅️ Volver a Configuración': 'settings',
        '⬅️ Retour aux Paramètres': 'settings',
        '⬅️ Zurück zu Einstellungen': 'settings',
        '⬅️ Torna alle Impostazioni': 'settings',
        '⬅️ Voltar às Configurações': 'settings',
        '⬅️ ወደ ቅንብሮች ይመለሱ': 'settings',
        # Account settings buttons
        '👤 View Profile': 'account_view_profile',
        '🔔 Notifications': 'account_notifications',
        '🗑️ Delete Account': 'account_delete',
        '⬅️ Back to Account': 'settings_account',
        '❌ Cancel': 'settings_account',
        # CV settings buttons
        '👁️ View Current CV': 'cv_view',
        '📤 Upload New CV': 'cv_upload',
        '🗑️ Remove CV': 'cv_remove',
        # Terms settings buttons
        '🔒 Privacy Policy': 'terms_privacy',
        # Back to CV
        '⬅️ Back to My CV': 'settings_cv',
        # Back to languages
        '⬅️ Back to Languages': 'settings_languages',
        '⬅️ Volver a Idiomas': 'settings_languages',
        '⬅️ Retour aux Langues': 'settings_languages',
        '⬅️ Zurück zu Sprachen': 'settings_languages',
        '⬅️ Torna alle Lingue': 'settings_languages',
        '⬅️ Voltar aos Idiomas': 'settings_languages',
        '⬅️ ወደ ቋንቋዎች ይመለሱ': 'settings_languages',
    }
    
    # Handle notification toggle buttons (dynamic text based on current state)
    if text.startswith("🚨 Job Alerts:"):
        if user_id not in _shared_notif_prefs:
            _shared_notif_prefs[user_id] = {'job_alerts': True, 'application_updates': True, 'messages': True, 'marketing': False}
        _shared_notif_prefs[user_id]['job_alerts'] = not _shared_notif_prefs[user_id]['job_alerts']
        await send_notification_settings(update, context)
        return
    elif text.startswith("📄 App Updates:"):
        if user_id not in _shared_notif_prefs:
            _shared_notif_prefs[user_id] = {'job_alerts': True, 'application_updates': True, 'messages': True, 'marketing': False}
        _shared_notif_prefs[user_id]['application_updates'] = not _shared_notif_prefs[user_id]['application_updates']
        await send_notification_settings(update, context)
        return
    elif text.startswith("💬 Messages:"):
        if user_id not in _shared_notif_prefs:
            _shared_notif_prefs[user_id] = {'job_alerts': True, 'application_updates': True, 'messages': True, 'marketing': False}
        _shared_notif_prefs[user_id]['messages'] = not _shared_notif_prefs[user_id]['messages']
        await send_notification_settings(update, context)
        return
    elif text.startswith("📢 Marketing:"):
        if user_id not in _shared_notif_prefs:
            _shared_notif_prefs[user_id] = {'job_alerts': True, 'application_updates': True, 'messages': True, 'marketing': False}
        _shared_notif_prefs[user_id]['marketing'] = not _shared_notif_prefs[user_id]['marketing']
        await send_notification_settings(update, context)
        return
    elif text in ("⚠️ YES, DELETE MY ACCOUNT",):
        user_id = update.effective_user.id
        delete_user(user_id)
        _shared_notif_prefs.pop(user_id, None)
        await update.effective_message.reply_text(
            "✅ Account Deleted Successfully\n\n"
            "Your account has been permanently deleted from HustleX.\n\n"
            "What was removed:\n"
            "- Profile information\n"
            "- Uploaded CV and documents\n"
            "- Notification preferences\n"
            "- All saved data\n\n"
            "Thank you for using HustleX. You can create a new account anytime by using /start.\n\n"
            "If you have feedback, contact @HustleXSupport",
            reply_markup=ReplyKeyboardRemove()
        )
        await show_menu(update, context)
        return
    elif text in ("❌ Cancel",) and not (
        context.user_data.get("awaiting_phone")
        or (is_user_registered(user_id) and not has_user_phone(user_id))
    ):
        pass  # Will fall through to menu_texts or ignore

    # Check if the text matches any menu item
    action = menu_texts.get(text)

    protected_actions = {
        'menu', 'profile', 'applications', 'about', 'settings',
        'settings_languages', 'settings_account', 'settings_cv', 'settings_terms',
        'account_view_profile', 'account_notifications', 'account_delete',
        'cv_view', 'cv_upload', 'cv_remove', 'terms_privacy',
    }
    if action is None:
        return

    if action in protected_actions and user_id not in registered_users and not is_user_registered(user_id):
        await show_registration_prompt(update, context)
        return
    
    if action == 'menu':
        await menu_callback(update, context)
    elif action == 'post_job_telegram':
        await post_job_start(update, context)
    elif action == 'post_job_website':
        # Post via website is a web app, but let's send a message
        await update.effective_message.reply_text(
            "🌐 *Post Job via Website*\n\nPlease use the web app to post your job.",
            parse_mode="Markdown"
        )
    elif action == 'profile':
        job_id = get_pending_job_id(context)
        profile_url = f"{WEBAPP_URL.rstrip('/')}/freelancer-profile-setup?job_id={job_id}"
        keyboard = [[InlineKeyboardButton("👤 Open Profile", web_app=WebAppInfo(url=profile_url))]]
        await update.effective_message.reply_text(
            "👤 *Your Profile Arsenal*\n\n"
            "Your profile is your **digital throne** — the kingdom where clients discover your genius. "
            "It's not just a page; it's your **24/7 sales machine**, your **silent pitch**, and the "
            "difference between \"maybe\" and \"hired.\"\n\n"
            "A complete profile = **3× more invites**, **5× more trust**, and clients fighting to work with you.\n\n"
            "What awaits you inside:\n"
            "• 🎯 **Battle Station** — Showcase skills that slay\n"
            "• 🌟 **Epic Portfolio** — Let your work do the talkin'\n"
            "• 📊 **Verified Badges** — Flex your credibility\n"
            "• 🚀 **Instant Apply** — One tap to your next gig\n\n"
            "This isn't just a profile — it's your **legacy in the making** 👑",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif action == 'applications':
        user_id = update.effective_user.id
        applications_url = f"https://hustlexet.vercel.app/my-applications?user_id={user_id}"
        keyboard = [[InlineKeyboardButton("📋 Open Applications", web_app=WebAppInfo(url=applications_url))]]
        await update.effective_message.reply_text(
            "📋 *Your Applications Command Center*\n\n"
            "This is where **opportunities meet their match** — every application is a **battle won** "
            "before the war even starts. You don't just apply; you **dominate**.\n\n"
            "Track your conquests:\n"
            "• 🎯 **Live Status** — Know exactly where you stand\n"
            "• ⚡ **Instant Replies** — Clients move at the speed of trust\n"
            "• 📊 **Win Rate** — Watch your hit rate climb\n"
            "• 🔔 **Smart Alerts** — Never miss a callback\n\n"
            "Every 'Submitted' is a **step closer to your empire**. "
            "Every 'Accepted' is a **crown on your legacy**.\n\n"
            "Let's go get that bag 💼🔥",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif action == 'about':
        about_text = (
            "🚀 *About HustleX*\n\n"
            "Welcome to *HustleX* – where *ambition meets opportunity!* ✨\n\n"
            "At HustleX, we believe talent has *no limits* 🌍. Whether you’re a designer 🎨, "
            "developer 💻, writer ✍️, or digital wizard 🪄, we connect skilled freelancers with "
            "clients who value *quality, creativity, and reliability*.\n\n"
            "*Our mission:* 💪 Elevate projects 📈 Transform careers 🌟\n\n"
            "*Why HustleX?*\n"
            "- *Seamless Experience:* Navigate your freelance journey effortlessly ⚡\n"
            "- *Trusted Connections:* Work with verified clients and freelancers 🤝\n"
            "- *Smart Tools:* Manage profiles, applications, and projects—all in Telegram 📲\n"
            "- *Growth-Focused:* Showcase your skills, build your reputation, and level up 🚀\n\n"
            "Join *HustleX* today and turn your skills into opportunities! 🔥 "
            "Because here, *every hustle counts* 💼💎"
        )
        await update.effective_message.reply_text(about_text, parse_mode="Markdown")
    elif action == 'settings':
        await settings_cb(update, context)
    elif action == 'settings_languages':
        await settings_languages_cb(update, context)
    elif action == 'settings_account':
        await settings_account_cb(update, context)
    elif action == 'settings_cv':
        await settings_cv_cb(update, context)
    elif action == 'settings_terms':
        await settings_terms_cb(update, context)
    elif action and action.startswith('lang_'):
        # Handle language selection
        lang_code = action.split('_')[1]
        user_languages[user_id] = lang_code
        # Now send a confirmation message!
        lang_names = {
            'en': '🇺🇸 English',
            'es': '🇪🇸 Español', 
            'fr': '🇫🇷 Français',
            'de': '🇩🇪 Deutsch',
            'it': '🇮🇹 Italiano',
            'pt': '🇵🇹 Português',
            'am': '🇪🇹 አማርኛ (Amharic)'
        }
        selected_lang = lang_names.get(lang_code, 'English')
        # Language-specific messages
        confirmation_messages = {
            'en': {
                'title': "✅ *Language Updated!*",
                'message': f"🌍 *Selected Language:* {selected_lang}\n\n📝 All bot messages will now be displayed in your selected language.",
                'back': "⬅️ Back to Languages"
            },
            'es': {
                'title': "✅ *¡Idioma Actualizado!*",
                'message': f"🌍 *Idioma Seleccionado:* {selected_lang}\n\n📝 Todos los mensajes del bot ahora se mostrarán en tu idioma seleccionado.",
                'back': "⬅️ Volver a Idiomas"
            },
            'fr': {
                'title': "✅ *Langue Mise à Jour!*",
                'message': f"🌍 *Langue Sélectionnée:* {selected_lang}\n\n📝 Tous les messages du bot s'afficheront maintenant dans votre langue sélectionnée.",
                'back': "⬅️ Retour aux Langues"
            },
            'de': {
                'title': "✅ *Sprache Aktualisiert!*",
                'message': f"🌍 *Ausgewählte Sprache:* {selected_lang}\n\n📝 Alle Bot-Nachrichten werden jetzt in Ihrer ausgewählten Sprache angezeigt.",
                'back': "⬅️ Zurück zu Sprachen"
            },
            'it': {
                'title': "✅ *Lingua Aggiornata!*",
                'message': f"🌍 *Lingua Selezionata:* {selected_lang}\n\n📝 Tutti i messaggi del bot ora verranno visualizzati nella tua lingua selezionata.",
                'back': "⬅️ Torna alle Lingue"
            },
            'pt': {
                'title': "✅ *Idioma Atualizado!*",
                'message': f"🌍 *Idioma Selecionado:* {selected_lang}\n\n📝 Todas as mensagens do bot agora serão exibidas no seu idioma selecionado.",
                'back': "⬅️ Voltar aos Idiomas"
            },
            'am': {
                'title': "✅ *ቋንቋ ተዘምኗል!*",
                'message': f"🌍 *የተመረጠ ቋንቋ:* {selected_lang}\n\n📝 ሁሉም የቦት መልዕክቶች አሁን በተመረጠዎ ቋንቋ ይታያሉ።",
                'back': "⬅️ ወደ ቋንቋዎች ይመለሱ"
            }
        }
        msg = confirmation_messages.get(lang_code, confirmation_messages['en'])
        keyboard = [[KeyboardButton(msg['back'])]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        await update.effective_message.reply_text(
            f"{msg['title']}\n\n{msg['message']}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    elif action == 'account_view_profile':
        try:
            user = update.effective_user
            if not user:
                await update.effective_message.reply_text("Error: could not identify user.")
                return
            profile = get_user_profile(user.id)
            pov = "Not set"
            name = profile.get('name') if profile else None
            age = profile.get('age') if profile else None
            sex = profile.get('sex') if profile else None
            phone = (profile.get('phone') or profile.get('phone_number')) if profile else None
            text = (
                f"👤 Your Profile\n\n"
                f"Personal Info:\n"
                f"- Name: {name or user.first_name or pov}\n"
                f"- Age: {age or pov}\n"
                f"- Gender: {sex or pov}\n"
                f"- Phone: {phone or pov}\n"
                f"- Username: @{user.username or pov}\n"
                f"- User ID: {user.id}\n\n"
                f"Your profile is your digital handshake - keep it fresh!"
            )
            keyboard = [[KeyboardButton("⬅️ Back to Settings")]]
            await update.effective_message.reply_text(
                text,
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            )
        except Exception as e:
            await update.effective_message.reply_text(f"Error: {type(e).__name__}: {e}")
    elif action == 'account_notifications':
        await send_notification_settings(update, context)
    elif action == 'account_delete':
        await send_delete_confirmation(update, context)
    elif action == 'cv_view':
        user_id = update.effective_user.id
        cv_data = get_user_cv(user_id)
        if cv_data is not None and cv_data.get("cv_file_data") is not None:
            file_bytes = cv_data["cv_file_data"]
            filename = cv_data.get("cv_filename", "cv.pdf")
            await update.effective_message.reply_text(f"👁️ *Your CV*\n\n📁 *File:* {filename}\n\n📎 Sending file...", parse_mode="Markdown")
            await context.bot.send_document(
                chat_id=user_id,
                document=io.BytesIO(file_bytes if isinstance(file_bytes, bytes) else bytes(file_bytes)),
                filename=filename,
                caption=f"📄 Your CV: {filename}"
            )
        else:
            await update.effective_message.reply_text("❌ No CV uploaded yet!", parse_mode="Markdown")
    elif action == 'cv_upload':
        await update.effective_message.reply_text("📤 *Upload CV*\n\nPlease send your CV file as a document (PDF or DOCX).", parse_mode="Markdown")
    elif action == 'cv_remove':
        user_id = update.effective_user.id
        cv_data = get_user_cv(user_id)
        if cv_data is not None and cv_data.get("cv_file_data") is not None:
            save_profile_fields(user_id, {
                "cv_file_data": None,
                "cv_filename": None,
                "cv_mime_type": None,
                "cv_file_size": None,
                "cv_upload_date": None,
            })
            user_cvs.pop(user_id, None)
            await update.effective_message.reply_text("✅ CV removed successfully!", parse_mode="Markdown")
        else:
            await update.effective_message.reply_text("❌ No CV to remove!", parse_mode="Markdown")
    elif action == 'terms_privacy':
        await update.effective_message.reply_text(
            "🔒 *Privacy Policy*\n\n"
            "We take your privacy seriously. Here's what we do:\n\n"
            "• We don't share your personal information without consent\n"
            "• CVs are stored securely and only shared with your consent\n"
            "• You can delete your data at any time",
            parse_mode="Markdown"
        )

# ---------------------------
# Other tab callbacks
# ---------------------------
async def applications_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    applications_url = f"https://hustlexet.vercel.app/my-applications?user_id={user_id}"
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(
            f"📋 *Applications*\n\nClick here to access your applications: {applications_url}",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            f"📋 *Applications*\n\nClick here to access your applications: {applications_url}",
            parse_mode="Markdown"
        )

async def about_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    about_text = (
        "🚀 *About HustleX*\n\n"
        "Welcome to *HustleX* – where *ambition meets opportunity!* ✨\n\n"
        "At HustleX, we believe talent has *no limits* 🌍. Whether you’re a designer 🎨, "
        "developer 💻, writer ✍️, or digital wizard 🪄, we connect skilled freelancers with "
        "clients who value *quality, creativity, and reliability*.\n\n"
        "*Our mission:* 💪 Elevate projects 📈 Transform careers 🌟\n\n"
        "*Why HustleX?*\n"
        "- *Seamless Experience:* Navigate your freelance journey effortlessly ⚡\n"
        "- *Trusted Connections:* Work with verified clients and freelancers 🤝\n"
        "- *Smart Tools:* Manage profiles, applications, and projects—all in Telegram 📲\n"
        "- *Growth-Focused:* Showcase your skills, build your reputation, and level up 🚀\n\n"
        "Join *HustleX* today and turn your skills into opportunities! 🔥 "
        "Because here, *every hustle counts* 💼💎"
    )
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(about_text, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(about_text, parse_mode="Markdown")

async def settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific settings messages
    settings_messages = {
        'en': {
            'title': "⚙️ *Settings*",
            'instruction': "Choose a category to manage your preferences:",
            'languages': "🌍 Languages",
            'account': "👤 Account",
            'cv': "📄 My CV",
            'terms': "📋 Terms and Conditions",
            'back': "⬅️ Back to Menu"
        },
        'es': {
            'title': "⚙️ *Configuración*",
            'instruction': "Elige una categoría para gestionar tus preferencias:",
            'languages': "🌍 Idiomas",
            'account': "👤 Cuenta",
            'cv': "📄 Mi CV",
            'terms': "📋 Términos y Condiciones",
            'back': "⬅️ Volver al Menú"
        },
        'fr': {
            'title': "⚙️ *Paramètres*",
            'instruction': "Choisissez une catégorie pour gérer vos préférences:",
            'languages': "🌍 Langues",
            'account': "👤 Compte",
            'cv': "📄 Mon CV",
            'terms': "📋 Termes et Conditions",
            'back': "⬅️ Retour au Menu"
        },
        'de': {
            'title': "⚙️ *Einstellungen*",
            'instruction': "Wählen Sie eine Kategorie zur Verwaltung Ihrer Einstellungen:",
            'languages': "🌍 Sprachen",
            'account': "👤 Konto",
            'cv': "📄 Mein Lebenslauf",
            'terms': "📋 Geschäftsbedingungen",
            'back': "⬅️ Zurück zum Menü"
        },
        'it': {
            'title': "⚙️ *Impostazioni*",
            'instruction': "Scegli una categoria per gestire le tue preferenze:",
            'languages': "🌍 Lingue",
            'account': "👤 Account",
            'cv': "📄 Il Mio CV",
            'terms': "📋 Termini e Condizioni",
            'back': "⬅️ Torna al Menu"
        },
        'pt': {
            'title': "⚙️ *Configurações*",
            'instruction': "Escolha uma categoria para gerenciar suas preferências:",
            'languages': "🌍 Idiomas",
            'account': "👤 Conta",
            'cv': "📄 Meu CV",
            'terms': "📋 Termos e Condições",
            'back': "⬅️ Voltar ao Menu"
        },
        'am': {
            'title': "⚙️ *ቅንብሮች*",
            'instruction': "የሚመርጡትን ለማስተካከል አንድ ምድብ ይምረጡ:",
            'languages': "🌍 ቋንቋዎች",
            'account': "👤 መለያ",
            'cv': "📄 የእኔ CV",
            'terms': "📋 ውሎች እና ሁኔታዎች",
            'back': "⬅️ ወደ ሜኑ ይመለሱ"
        }
    }
    
    messages = settings_messages.get(lang_code, settings_messages['en'])
    
    keyboard = [
        [KeyboardButton(messages['languages']), KeyboardButton(messages['account'])],
        [KeyboardButton(messages['cv']), KeyboardButton(messages['terms'])],
        [KeyboardButton(messages['back'])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit_message(
            q,
            f"{messages['title']}\n\n{messages['instruction']}",
            reply_markup=reply_markup,
            parse_mode="Markdown",
            context=context
        )
    else:
        await update.effective_message.reply_text(
            f"{messages['title']}\n\n{messages['instruction']}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# ---------------------------
# Settings Tab Callbacks
# ---------------------------
async def settings_languages_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_lang = user_languages.get(user_id, 'en')
    
    lang_names = {
        'en': '🇺🇸 English',
        'es': '🇪🇸 Español', 
        'fr': '🇫🇷 Français',
        'de': '🇩🇪 Deutsch',
        'it': '🇮🇹 Italiano',
        'pt': '🇵🇹 Português',
        'am': '🇪🇹 አማርኛ (Amharic)'
    }
    
    current_lang_name = lang_names.get(current_lang, 'English')
    
    # Language-specific messages
    messages = {
        'en': {
            'title': "🌍 *Language Settings*",
            'instruction': "Select your preferred language:",
            'current': f"📝 *Current:* {current_lang_name}",
            'tip': "💡 *Tip:* Language changes will apply to all bot messages.",
            'back': "⬅️ Back to Settings"
        },
        'es': {
            'title': "🌍 *Configuración de Idioma*",
            'instruction': "Selecciona tu idioma preferido:",
            'current': f"📝 *Actual:* {current_lang_name}",
            'tip': "💡 *Consejo:* Los cambios de idioma se aplicarán a todos los mensajes del bot.",
            'back': "⬅️ Volver a Configuración"
        },
        'fr': {
            'title': "🌍 *Paramètres de Langue*",
            'instruction': "Sélectionnez votre langue préférée:",
            'current': f"📝 *Actuel:* {current_lang_name}",
            'tip': "💡 *Conseil:* Les changements de langue s'appliqueront à tous les messages du bot.",
            'back': "⬅️ Retour aux Paramètres"
        },
        'de': {
            'title': "🌍 *Spracheinstellungen*",
            'instruction': "Wählen Sie Ihre bevorzugte Sprache:",
            'current': f"📝 *Aktuell:* {current_lang_name}",
            'tip': "💡 *Tipp:* Sprachänderungen gelten für alle Bot-Nachrichten.",
            'back': "⬅️ Zurück zu Einstellungen"
        },
        'it': {
            'title': "🌍 *Impostazioni Lingua*",
            'instruction': "Seleziona la tua lingua preferita:",
            'current': f"📝 *Attuale:* {current_lang_name}",
            'tip': "💡 *Suggerimento:* Le modifiche della lingua si applicheranno a tutti i messaggi del bot.",
            'back': "⬅️ Torna alle Impostazioni"
        },
        'pt': {
            'title': "🌍 *Configurações de Idioma*",
            'instruction': "Selecione seu idioma preferido:",
            'current': f"📝 *Atual:* {current_lang_name}",
            'tip': "💡 *Dica:* As mudanças de idioma se aplicarão a todas as mensagens do bot.",
            'back': "⬅️ Voltar às Configurações"
        },
        'am': {
            'title': "🌍 *የቋንቋ ቅንብሮች*",
            'instruction': "የሚመርጡትን ቋንቋ ይምረጡ:",
            'current': f"📝 *አሁን ያለ:* {current_lang_name}",
            'tip': "💡 *ምክር:* የቋንቋ ለውጦች ለሁሉም የቦት መልዕክቶች ይተገበራሉ።",
            'back': "⬅️ ወደ ቅንብሮች ይመለሱ"
        }
    }
    
    msg = messages.get(current_lang, messages['en'])
    
    keyboard = [
        [KeyboardButton("🇺🇸 English"), KeyboardButton("🇪🇸 Español")],
        [KeyboardButton("🇫🇷 Français"), KeyboardButton("🇩🇪 Deutsch")],
        [KeyboardButton("🇮🇹 Italiano"), KeyboardButton("🇵🇹 Português")],
        [KeyboardButton("🇪🇹 አማርኛ (Amharic)")],
        [KeyboardButton(msg['back'])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit_message(
            q,
            f"{msg['title']}\n\n{msg['instruction']}\n\n{msg['current']}\n{msg['tip']}",
            reply_markup=reply_markup,
            parse_mode="Markdown",
            context=context
        )
    else:
        await update.effective_message.reply_text(
            f"{msg['title']}\n\n{msg['instruction']}\n\n{msg['current']}\n{msg['tip']}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def settings_account_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if not user:
            await update.effective_message.reply_text("Error: could not identify user. Try /start again.")
            return
        profile = get_user_profile(user.id)
        pov = "Not set"
        account_text = (
            f"👤 Account Settings\n\n"
            f"Personal Info:\n"
            f"- Name: {(profile.get('name') if profile else None) or user.first_name or pov}\n"
            f"- Age: {(profile.get('age') if profile else None) or pov}\n"
            f"- Gender: {(profile.get('sex') if profile else None) or pov}\n"
            f"- Phone: {(profile.get('phone') or profile.get('phone_number') if profile else None) or pov}\n"
            f"- Username: @{user.username or pov}\n"
            f"- User ID: {user.id}\n\n"
            f"Manage your account below:"
        )
        keyboard = [
            [KeyboardButton("👤 View Profile"), KeyboardButton("🔔 Notifications")],
            [KeyboardButton("🗑️ Delete Account"), KeyboardButton("⬅️ Back to Settings")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        if update.callback_query:
            q = update.callback_query
            await q.answer()
            await safe_edit_message(q, account_text, reply_markup=reply_markup, context=context)
        else:
            await update.effective_message.reply_text(account_text, reply_markup=reply_markup)
    except Exception as e:
        try:
            await update.effective_message.reply_text(f"Error: {type(e).__name__}: {e}")
        except:
            pass

async def settings_cv_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cv_data = get_user_cv(user_id)
    has_cv = cv_data is not None and cv_data.get("cv_file_data") is not None
    
    if has_cv:
        filename = cv_data.get("cv_filename", "Unknown")
        keyboard = [
            [KeyboardButton("👁️ View Current CV"), KeyboardButton("📤 Upload New CV")],
            [KeyboardButton("🗑️ Remove CV"), KeyboardButton("⬅️ Back to Settings")]
        ]
        status_text = f"✅ CV uploaded: {filename}"
    else:
        keyboard = [
            [KeyboardButton("📤 Upload New CV"), KeyboardButton("⬅️ Back to Settings")]
        ]
        status_text = "❌ No CV uploaded"
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit_message(
            q,
            f"📄 *My CV*\n\n"
            f"📁 *Current Status:* {status_text}\n"
            f"📝 *Supported formats:* PDF, DOCX\n"
            f"📏 *Max file size:* 16 MB\n\n"
            f"💡 *Tip:* A well-formatted CV increases your chances of getting hired!\n\n"
            f"Choose an option below:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
            context=context
        )
    else:
        await update.effective_message.reply_text(
            f"📄 *My CV*\n\n"
            f"📁 *Current Status:* {status_text}\n"
            f"📝 *Supported formats:* PDF, DOCX\n"
            f"📏 *Max file size:* 16 MB\n\n"
            f"💡 *Tip:* A well-formatted CV increases your chances of getting hired!\n\n"
            f"Choose an option below:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def settings_terms_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("🔒 Privacy Policy"), KeyboardButton("⬅️ Back to Settings")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    terms_text = (
        "📋 *Terms and Conditions*\n\n"
        "📄 *Last Updated:* October 2024\n\n"
        "Welcome to HustleX! By using our bot, you agree to these terms:\n\n"
        "✅ *Usage Rights:*\n"
        "• You may use HustleX for legitimate job searching and posting\n"
        "• All posted jobs must be real and legal opportunities\n\n"
        "🚫 *Prohibited Activities:*\n"
        "• Posting fake or misleading job offers\n"
        "• Spam or harassment of other users\n"
        "• Sharing inappropriate content\n\n"
        "🛡️ *Privacy:*\n"
        "• We protect your personal information\n"
        "• CVs are stored securely and only shared with your consent\n\n"
        "📞 *Contact:* @HustleXSupport for questions"
    )
    
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit_message(
            q,
            terms_text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
            context=context
        )
    else:
        await update.effective_message.reply_text(
            terms_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# ---------------------------
# Additional Settings Handlers
# ---------------------------
async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # Extract language code from callback data
    lang_code = q.data.split('_')[1]
    user_id = update.effective_user.id
    
    # Store the user's language preference
    user_languages[user_id] = lang_code
    
    lang_names = {
        'en': '🇺🇸 English',
        'es': '🇪🇸 Español', 
        'fr': '🇫🇷 Français',
        'de': '🇩🇪 Deutsch',
        'it': '🇮🇹 Italiano',
        'pt': '🇵🇹 Português',
        'am': '🇪🇹 አማርኛ (Amharic)'
    }
    
    selected_lang = lang_names.get(lang_code, 'English')
    
    # Language-specific confirmation messages
    confirmation_messages = {
        'en': {
            'title': "✅ *Language Updated!*",
            'message': f"🌍 *Selected Language:* {selected_lang}\n\n📝 All bot messages will now be displayed in your selected language.",
            'back': "⬅️ Back to Languages"
        },
        'es': {
            'title': "✅ *¡Idioma Actualizado!*",
            'message': f"🌍 *Idioma Seleccionado:* {selected_lang}\n\n📝 Todos los mensajes del bot ahora se mostrarán en tu idioma seleccionado.",
            'back': "⬅️ Volver a Idiomas"
        },
        'fr': {
            'title': "✅ *Langue Mise à Jour!*",
            'message': f"🌍 *Langue Sélectionnée:* {selected_lang}\n\n📝 Tous les messages du bot s'afficheront maintenant dans votre langue sélectionnée.",
            'back': "⬅️ Retour aux Langues"
        },
        'de': {
            'title': "✅ *Sprache Aktualisiert!*",
            'message': f"🌍 *Ausgewählte Sprache:* {selected_lang}\n\n📝 Alle Bot-Nachrichten werden jetzt in Ihrer ausgewählten Sprache angezeigt.",
            'back': "⬅️ Zurück zu Sprachen"
        },
        'it': {
            'title': "✅ *Lingua Aggiornata!*",
            'message': f"🌍 *Lingua Selezionata:* {selected_lang}\n\n📝 Tutti i messaggi del bot ora verranno visualizzati nella tua lingua selezionata.",
            'back': "⬅️ Torna alle Lingue"
        },
        'pt': {
            'title': "✅ *Idioma Atualizado!*",
            'message': f"🌍 *Idioma Selecionado:* {selected_lang}\n\n📝 Todas as mensagens do bot agora serão exibidas no seu idioma selecionado.",
            'back': "⬅️ Voltar aos Idiomas"
        },
        'am': {
            'title': "✅ *ቋንቋ ተዘምኗል!*",
            'message': f"🌍 *የተመረጠ ቋንቋ:* {selected_lang}\n\n📝 ሁሉም የቦት መልዕክቶች አሁን በተመረጠዎ ቋንቋ ይታያሉ።",
            'back': "⬅️ ወደ ቋንቋዎች ይመለሱ"
        }
    }
    
    messages = confirmation_messages.get(lang_code, confirmation_messages['en'])
    
    keyboard = [
        [InlineKeyboardButton(messages['back'], callback_data="settings_languages")]
    ]
    
    await safe_edit_message(
        q,
        f"{messages['title']}\n\n{messages['message']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )

async def cv_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back to My CV", callback_data="settings_cv")]
    ]
    
    await safe_edit_message(
        q,
        "📤 *Upload CV*\n\n"
        "📎 Please send your CV file as a document.\n\n"
        "📝 *Supported formats:*\n"
        "• PDF (.pdf)\n"
        "• Word Document (.docx)\n\n"
        "📏 *Requirements:*\n"
        "• Maximum file size: 16 MB\n"
        "• File should be clearly readable\n\n"
        "💡 *Tip:* Make sure your CV is up-to-date with your latest experience!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )

async def cv_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = update.effective_user.id
    cv_data = get_user_cv(user_id)
    
    if cv_data is not None and cv_data.get("cv_file_data") is not None:
        file_bytes = cv_data["cv_file_data"]
        filename = cv_data.get("cv_filename", "cv.pdf")
        mime_type = cv_data.get("cv_mime_type", "application/pdf")
        
        # Send the CV file directly to the user
        await q.edit_message_text(
            f"👁️ *Sending your CV...*\n\n"
            f"📁 *File:* {filename}",
            parse_mode="Markdown"
        )
        
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(file_bytes if isinstance(file_bytes, bytes) else bytes(file_bytes)),
            filename=filename,
            caption=f"📄 Your CV: {filename}"
        )
        
        keyboard = [
            [InlineKeyboardButton("📤 Upload New CV", callback_data="cv_upload")],
            [InlineKeyboardButton("🗑️ Remove CV", callback_data="cv_remove")],
            [InlineKeyboardButton("⬅️ Back to My CV", callback_data="settings_cv")]
        ]
        
        await context.bot.send_message(
            chat_id=user_id,
            text="💼 *Want to update or remove your CV?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📤 Upload New CV", callback_data="cv_upload")],
            [InlineKeyboardButton("⬅️ Back to My CV", callback_data="settings_cv")]
        ]
        
        await safe_edit_message(
            q,
            "👁️ *View CV*\n\n"
            "📁 *Status:* No CV uploaded yet\n\n"
            "📝 Once you upload a CV, you'll be able to:\n"
            "• Preview your CV\n"
            "• Download a copy\n"
            "• Share it with potential employers\n"
            "• Update it anytime\n\n"
            "💼 *Ready to upload your CV?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
            context=context
        )

# ---------------------------
# CV Removal Handler
# ---------------------------
async def cv_remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user_id = update.effective_user.id
    cv_data = get_user_cv(user_id)
    has_cv = cv_data is not None and cv_data.get("cv_file_data") is not None
    
    if has_cv:
        # Remove CV from MongoDB
        save_profile_fields(user_id, {
            "cv_file_data": None,
            "cv_filename": None,
            "cv_mime_type": None,
            "cv_file_size": None,
            "cv_upload_date": None,
        })
        
        # Remove from in-memory too
        user_cvs.pop(user_id, None)
        
        keyboard = [
            [InlineKeyboardButton("📤 Upload New CV", callback_data="cv_upload")],
            [InlineKeyboardButton("⬅️ Back to My CV", callback_data="settings_cv")]
        ]
        
        await safe_edit_message(
            q,
            "🗑️ *CV Removed Successfully*\n\n"
            "✅ Your CV has been permanently deleted from our system.\n\n"
            "📝 *What's next?*\n"
            "• Upload a new CV anytime\n"
            "• Your profile remains active\n"
            "• Previous job applications are unaffected\n\n"
            "💼 *Ready to upload a new CV?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
            context=context
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📤 Upload CV", callback_data="cv_upload")],
            [InlineKeyboardButton("⬅️ Back to My CV", callback_data="settings_cv")]
        ]
        
        await safe_edit_message(
            q,
            "❌ *No CV Found*\n\n"
            "There's no CV to remove. You haven't uploaded one yet.\n\n"
            "💼 *Want to upload your CV?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
            context=context
        )

# ---------------------------
# Account Management Handlers
# ---------------------------
async def account_edit_profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user = update.effective_user
    user_id = user.id
    
    # Inherit profile from Telegram only
    name = f"{user.first_name or 'Not set'} {user.last_name or ''}".strip()
    telegram_username = f"@{user.username}" if user.username else "Not set"
    has_photo = False
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Account", callback_data="settings_account")]
    ]
    
    await safe_edit_message(
        q,
        f"👤 *Profile*\n\n"
        f"• Name: {name}\n"
        f"• Username: {telegram_username}\n"
        f"• User ID: {user.id}\n\n"
        f"ℹ️ Profile data is inherited from Telegram and not editable here.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )

# Name editing handler
async def edit_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # Get user's language preference
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific messages
    title = {
        'en': "📝 *Edit Name*",
        'es': "📝 *Editar Nombre*",
        'fr': "📝 *Modifier le Nom*",
        'de': "📝 *Name Bearbeiten*",
        'it': "📝 *Modifica Nome*",
        'pt': "📝 *Editar Nome*",
        'am': "📝 *ስም ማስተካከል*"
    }.get(lang_code, "📝 *Edit Name*")
    
    prompt = {
        'en': "Please send your new name as a message.",
        'es': "Por favor, envía tu nuevo nombre como mensaje.",
        'fr': "Veuillez envoyer votre nouveau nom en message.",
        'de': "Bitte senden Sie Ihren neuen Namen als Nachricht.",
        'it': "Invia il tuo nuovo nome come messaggio.",
        'pt': "Por favor, envie seu novo nome como mensagem.",
        'am': "እባክዎ አዲስ ስምዎን እንደ መልዕክት ይላኩ።"
    }.get(lang_code, "Please send your new name as a message.")
    
    note = {
        'en': "💡 *Note:* This will be used for your HustleX profile and job applications.",
        'es': "💡 *Nota:* Esto se utilizará para tu perfil de HustleX y solicitudes de trabajo.",
        'fr': "💡 *Remarque:* Ceci sera utilisé pour votre profil HustleX et vos candidatures.",
        'de': "💡 *Hinweis:* Dies wird für Ihr HustleX-Profil und Bewerbungen verwendet.",
        'it': "💡 *Nota:* Questo sarà utilizzato per il tuo profilo HustleX e le candidature di lavoro.",
        'pt': "💡 *Nota:* Isso será usado para seu perfil HustleX e candidaturas a empregos.",
        'am': "💡 *ማሳሰቢያ:* ይህ ለ HustleX መገለጫዎ እና ለሥራ ማመልከቻዎች ጥቅም ላይ ይውላል።"
    }.get(lang_code, "💡 *Note:* This will be used for your HustleX profile and job applications.")
    
    back_text = {
        'en': "⬅️ Back to Profile",
        'es': "⬅️ Volver al Perfil",
        'fr': "⬅️ Retour au Profil",
        'de': "⬅️ Zurück zum Profil",
        'it': "⬅️ Torna al Profilo",
        'pt': "⬅️ Voltar ao Perfil",
        'am': "⬅️ ወደ መገለጫ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Profile")
    
    keyboard = [
        [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
    ]
    
    await safe_edit_message(
        q,
        f"{title}\n\n"
        f"{prompt}\n\n"
        f"{note}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )
    
    # Store that we're waiting for a name input
    context.user_data['awaiting_input'] = 'name'

# Contact info editing handler
async def edit_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # Get user's language preference
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific messages
    title = {
        'en': "📞 *Edit Contact Information*",
        'es': "📞 *Editar Información de Contacto*",
        'fr': "📞 *Modifier les Coordonnées*",
        'de': "📞 *Kontaktinformationen Bearbeiten*",
        'it': "📞 *Modifica Informazioni di Contatto*",
        'pt': "📞 *Editar Informações de Contato*",
        'am': "📞 *የመገኛ መረጃ ማስተካከል*"
    }.get(lang_code, "📞 *Edit Contact Information*")
    
    prompt = {
        'en': "Please send your contact information as a message.",
        'es': "Por favor, envía tu información de contacto como mensaje.",
        'fr': "Veuillez envoyer vos coordonnées en message.",
        'de': "Bitte senden Sie Ihre Kontaktinformationen als Nachricht.",
        'it': "Invia le tue informazioni di contatto come messaggio.",
        'pt': "Por favor, envie suas informações de contato como mensagem.",
        'am': "እባክዎ የመገኛ መረጃዎን እንደ መልዕክት ይላኩ።"
    }.get(lang_code, "Please send your contact information as a message.")
    
    tip = {
        'en': "💡 *Tip:* You can include email, phone number, or other preferred contact methods.",
        'es': "💡 *Consejo:* Puedes incluir correo electrónico, número de teléfono u otros métodos de contacto preferidos.",
        'fr': "💡 *Conseil:* Vous pouvez inclure email, numéro de téléphone ou autres méthodes de contact préférées.",
        'de': "💡 *Tipp:* Sie können E-Mail, Telefonnummer oder andere bevorzugte Kontaktmethoden angeben.",
        'it': "💡 *Suggerimento:* Puoi includere email, numero di telefono o altri metodi di contatto preferiti.",
        'pt': "💡 *Dica:* Você pode incluir email, número de telefone ou outros métodos de contato preferidos.",
        'am': "💡 *ምክር:* ኢሜይል፣ ስልክ ቁጥር ወይም ሌሎች የሚመርጧቸውን የመገናኛ ዘዴዎች ማካተት ይችላሉ።"
    }.get(lang_code, "💡 *Tip:* You can include email, phone number, or other preferred contact methods.")
    
    back_text = {
        'en': "⬅️ Back to Profile",
        'es': "⬅️ Volver al Perfil",
        'fr': "⬅️ Retour au Profil",
        'de': "⬅️ Zurück zum Profil",
        'it': "⬅️ Torna al Profilo",
        'pt': "⬅️ Voltar ao Perfil",
        'am': "⬅️ ወደ መገለጫ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Profile")
    
    keyboard = [
        [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
    ]
    
    await safe_edit_message(
        q,
        f"{title}\n\n"
        f"{prompt}\n\n"
        f"{tip}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )
    
    # Store that we're waiting for contact info input
    context.user_data['awaiting_input'] = 'contact'

# Age editing handler
async def edit_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # Get user's language preference
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific messages
    title = {
        'en': "🎂 *Edit Age*",
        'es': "🎂 *Editar Edad*",
        'fr': "🎂 *Modifier l'Âge*",
        'de': "🎂 *Alter Bearbeiten*",
        'it': "🎂 *Modifica Età*",
        'pt': "🎂 *Editar Idade*",
        'am': "🎂 *እድሜ ማስተካከል*"
    }.get(lang_code, "🎂 *Edit Age*")
    
    prompt = {
        'en': "Please send your age as a number.",
        'es': "Por favor, envía tu edad como un número.",
        'fr': "Veuillez envoyer votre âge sous forme de nombre.",
        'de': "Bitte senden Sie Ihr Alter als Zahl.",
        'it': "Invia la tua età come numero.",
        'pt': "Por favor, envie sua idade como um número.",
        'am': "እባክዎ እድሜዎን እንደ ቁጥር ይላኩ።"
    }.get(lang_code, "Please send your age as a number.")
    
    note = {
        'en': "💡 *Note:* This information will be used for job matching and statistics.",
        'es': "💡 *Nota:* Esta información se utilizará para la coincidencia de trabajos y estadísticas.",
        'fr': "💡 *Remarque:* Ces informations seront utilisées pour la correspondance d'emploi et les statistiques.",
        'de': "💡 *Hinweis:* Diese Information wird für Job-Matching und Statistiken verwendet.",
        'it': "💡 *Nota:* Queste informazioni saranno utilizzate per l'abbinamento di lavoro e le statistiche.",
        'pt': "💡 *Nota:* Esta informação será usada para correspondência de emprego e estatísticas.",
        'am': "💡 *ማሳሰቢያ:* ይህ መረጃ ለሥራ ማዛመድ እና ለስታቲስቲክስ ጥቅም ላይ ይውላል።"
    }.get(lang_code, "💡 *Note:* This information will be used for job matching and statistics.")
    
    back_text = {
        'en': "⬅️ Back to Profile",
        'es': "⬅️ Volver al Perfil",
        'fr': "⬅️ Retour au Profil",
        'de': "⬅️ Zurück zum Profil",
        'it': "⬅️ Torna al Profilo",
        'pt': "⬅️ Voltar ao Perfil",
        'am': "⬅️ ወደ መገለጫ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Profile")
    
    keyboard = [
        [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
    ]
    
    await safe_edit_message(
        q,
        f"{title}\n\n"
        f"{prompt}\n\n"
        f"{note}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )
    
    # Store that we're waiting for age input
    context.user_data['awaiting_input'] = 'age'

# Profile photo update handler
async def edit_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # Get user's language preference
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific messages
    title = {
        'en': "📸 *Update Profile Photo*",
        'es': "📸 *Actualizar Foto de Perfil*",
        'fr': "📸 *Mettre à Jour la Photo de Profil*",
        'de': "📸 *Profilbild Aktualisieren*",
        'it': "📸 *Aggiorna Foto Profilo*",
        'pt': "📸 *Atualizar Foto de Perfil*",
        'am': "📸 *የመገለጫ ፎቶ ማዘመን*"
    }.get(lang_code, "📸 *Update Profile Photo*")
    
    prompt = {
        'en': "Please send a new photo for your profile.",
        'es': "Por favor, envía una nueva foto para tu perfil.",
        'fr': "Veuillez envoyer une nouvelle photo pour votre profil.",
        'de': "Bitte senden Sie ein neues Foto für Ihr Profil.",
        'it': "Invia una nuova foto per il tuo profilo.",
        'pt': "Por favor, envie uma nova foto para o seu perfil.",
        'am': "እባክዎ ለመገለጫዎ አዲስ ፎቶ ይላኩ።"
    }.get(lang_code, "Please send a new photo for your profile.")
    
    tip = {
        'en': "💡 *Tip:* A professional profile photo increases your chances of getting hired!",
        'es': "💡 *Consejo:* ¡Una foto de perfil profesional aumenta tus posibilidades de ser contratado!",
        'fr': "💡 *Conseil:* Une photo de profil professionnelle augmente vos chances d'être embauché !",
        'de': "💡 *Tipp:* Ein professionelles Profilbild erhöht Ihre Chancen, eingestellt zu werden!",
        'it': "💡 *Suggerimento:* Una foto profilo professionale aumenta le tue possibilità di essere assunto!",
        'pt': "💡 *Dica:* Uma foto de perfil profissional aumenta suas chances de ser contratado!",
        'am': "💡 *ምክር:* ሙያዊ የመገለጫ ፎቶ የመቀጠር እድልዎን ይጨምራል!"
    }.get(lang_code, "💡 *Tip:* A professional profile photo increases your chances of getting hired!")
    
    back_text = {
        'en': "⬅️ Back to Profile",
        'es': "⬅️ Volver al Perfil",
        'fr': "⬅️ Retour au Profil",
        'de': "⬅️ Zurück zum Profil",
        'it': "⬅️ Torna al Profilo",
        'pt': "⬅️ Voltar ao Perfil",
        'am': "⬅️ ወደ መገለጫ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Profile")
    
    keyboard = [
        [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
    ]
    
    await safe_edit_message(
        q,
        f"{title}\n\n"
        f"{prompt}\n\n"
        f"{tip}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        context=context
    )
    
    # Store that we're waiting for a photo input
    context.user_data['awaiting_input'] = 'photo'

# Handler for text messages when awaiting specific input
async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Get user's language preference
    lang_code = user_languages.get(user_id, 'en')
    
    # Initialize user profile if not exists
    if user_id not in user_profiles:
        user_profiles[user_id] = {}
    
    # Language-specific back button text
    back_text = {
        'en': "⬅️ Back to Profile",
        'es': "⬅️ Volver al Perfil",
        'fr': "⬅️ Retour au Profil",
        'de': "⬅️ Zurück zum Profil",
        'it': "⬅️ Torna al Profilo",
        'pt': "⬅️ Voltar ao Perfil",
        'am': "⬅️ ወደ መገለጫ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Profile")
    
    keyboard = [
        [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
    ]
    
    # Check what kind of input we're waiting for
    if context.user_data.get('awaiting_input') == 'name':
        # Store the new name
        user_profiles[user_id]['custom_name'] = message_text
        
        # Clear the awaiting input flag
        context.user_data.pop('awaiting_input', None)
        
        # Language-specific messages
        title = {
            'en': "✅ *Name Updated Successfully!*",
            'es': "✅ *¡Nombre Actualizado Exitosamente!*",
            'fr': "✅ *Nom Mis à Jour avec Succès!*",
            'de': "✅ *Name Erfolgreich Aktualisiert!*",
            'it': "✅ *Nome Aggiornato con Successo!*",
            'pt': "✅ *Nome Atualizado com Sucesso!*",
            'am': "✅ *ስም በተሳካ ሁኔታ ተዘምኗል!*"
        }.get(lang_code, "✅ *Name Updated Successfully!*")
        
        updated_to = {
            'en': f"Your profile name has been changed to: *{message_text}*",
            'es': f"Tu nombre de perfil ha sido cambiado a: *{message_text}*",
            'fr': f"Votre nom de profil a été changé à: *{message_text}*",
            'de': f"Ihr Profilname wurde geändert zu: *{message_text}*",
            'it': f"Il tuo nome del profilo è stato cambiato a: *{message_text}*",
            'pt': f"Seu nome de perfil foi alterado para: *{message_text}*",
            'am': f"የመገለጫ ስምዎ ወደ: *{message_text}* ተቀይሯል"
        }.get(lang_code, f"Your profile name has been changed to: *{message_text}*")
        
        note = {
            'en': "This name will be used for all your HustleX activities.",
            'es': "Este nombre se utilizará para todas tus actividades en HustleX.",
            'fr': "Ce nom sera utilisé pour toutes vos activités HustleX.",
            'de': "Dieser Name wird für alle Ihre HustleX-Aktivitäten verwendet.",
            'it': "Questo nome sarà utilizzato per tutte le tue attività su HustleX.",
            'pt': "Este nome será usado para todas as suas atividades no HustleX.",
            'am': "ይህ ስም ለሁሉም የHustleX እንቅስቃሴዎችዎ ጥቅም ላይ ይውላል።"
        }.get(lang_code, "This name will be used for all your HustleX activities.")
        
        # Save profile to API
        await save_profile_to_api(user_id, user_profiles[user_id])
        
        await update.message.reply_text(
            f"{title}\n\n"
            f"{updated_to}\n\n"
            f"{note}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif context.user_data.get('awaiting_input') == 'contact':
        # Store the new contact info
        text_input_handler.user_profiles[user_id]['contact_info'] = message_text
        
        # Clear the awaiting input flag
        context.user_data.pop('awaiting_input', None)
        
        # Language-specific messages
        title = {
            'en': "✅ *Contact Information Updated Successfully!*",
            'es': "✅ *¡Información de Contacto Actualizada Exitosamente!*",
            'fr': "✅ *Coordonnées Mises à Jour avec Succès!*",
            'de': "✅ *Kontaktinformationen Erfolgreich Aktualisiert!*",
            'it': "✅ *Informazioni di Contatto Aggiornate con Successo!*",
            'pt': "✅ *Informações de Contato Atualizadas com Sucesso!*",
            'am': "✅ *የመገኛ መረጃ በተሳካ ሁኔታ ተዘምኗል!*"
        }.get(lang_code, "✅ *Contact Information Updated Successfully!*")
        
        updated_to = {
            'en': f"Your contact information has been updated to: *{message_text}*",
            'es': f"Tu información de contacto ha sido actualizada a: *{message_text}*",
            'fr': f"Vos coordonnées ont été mises à jour à: *{message_text}*",
            'de': f"Ihre Kontaktinformationen wurden aktualisiert auf: *{message_text}*",
            'it': f"Le tue informazioni di contatto sono state aggiornate a: *{message_text}*",
            'pt': f"Suas informações de contato foram atualizadas para: *{message_text}*",
            'am': f"የመገኛ መረጃዎ ወደ: *{message_text}* ተዘምኗል"
        }.get(lang_code, f"Your contact information has been updated to: *{message_text}*")
        
        note = {
            'en': "This will be used for employers to reach you.",
            'es': "Esto será utilizado por los empleadores para contactarte.",
            'fr': "Cela sera utilisé par les employeurs pour vous contacter.",
            'de': "Dies wird von Arbeitgebern verwendet, um Sie zu kontaktieren.",
            'it': "Questo sarà utilizzato dai datori di lavoro per contattarti.",
            'pt': "Isso será usado pelos empregadores para entrar em contato com você.",
            'am': "ይህ አሰሪዎች እርስዎን ለማግኘት ጥቅም ላይ ይውላል።"
        }.get(lang_code, "This will be used for employers to reach you.")
        
        # Save profile to API
        await save_profile_to_api(user_id, user_profiles[user_id])
        
        await update.message.reply_text(
            f"{title}\n\n"
            f"{updated_to}\n\n"
            f"{note}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif context.user_data.get('awaiting_input') == 'age':
        # Validate age input
        try:
            age = int(message_text)
            if age < 16 or age > 100:
                # Invalid age error messages
                error_title = {
                    'en': "❌ *Invalid Age*",
                    'es': "❌ *Edad Inválida*",
                    'fr': "❌ *Âge Invalide*",
                    'de': "❌ *Ungültiges Alter*",
                    'it': "❌ *Età Non Valida*",
                    'pt': "❌ *Idade Inválida*",
                    'am': "❌ *ልክ ያልሆነ እድሜ*"
                }.get(lang_code, "❌ *Invalid Age*")
                
                error_msg = {
                    'en': "Please enter a valid age between 16 and 100.",
                    'es': "Por favor, introduce una edad válida entre 16 y 100.",
                    'fr': "Veuillez entrer un âge valide entre 16 et 100.",
                    'de': "Bitte geben Sie ein gültiges Alter zwischen 16 und 100 ein.",
                    'it': "Inserisci un'età valida tra 16 e 100.",
                    'pt': "Por favor, insira uma idade válida entre 16 e 100.",
                    'am': "እባክዎ ከ16 እና 100 መካከል ያለ ትክክለኛ እድሜ ያስገቡ።"
                }.get(lang_code, "Please enter a valid age between 16 and 100.")
                
                await update.message.reply_text(
                    f"{error_title}\n\n"
                    f"{error_msg}",
                    parse_mode="Markdown"
                )
                return
                
            # Store the new age
            user_profiles[user_id]['age'] = age
            
            # Clear the awaiting input flag
            context.user_data.pop('awaiting_input', None)
            
            # Language-specific messages
            title = {
                'en': "✅ *Age Updated Successfully!*",
                'es': "✅ *¡Edad Actualizada Exitosamente!*",
                'fr': "✅ *Âge Mis à Jour avec Succès!*",
                'de': "✅ *Alter Erfolgreich Aktualisiert!*",
                'it': "✅ *Età Aggiornata con Successo!*",
                'pt': "✅ *Idade Atualizada com Sucesso!*",
                'am': "✅ *እድሜ በተሳካ ሁኔታ ተዘምኗል!*"
            }.get(lang_code, "✅ *Age Updated Successfully!*")
            
            updated_to = {
                'en': f"Your age has been updated to: *{age}*",
                'es': f"Tu edad ha sido actualizada a: *{age}*",
                'fr': f"Votre âge a été mis à jour à: *{age}*",
                'de': f"Ihr Alter wurde aktualisiert auf: *{age}*",
                'it': f"La tua età è stata aggiornata a: *{age}*",
                'pt': f"Sua idade foi atualizada para: *{age}*",
                'am': f"እድሜዎ ወደ: *{age}* ተዘምኗል"
            }.get(lang_code, f"Your age has been updated to: *{age}*")
            
            note = {
                'en': "This information will be used for job matching.",
                'es': "Esta información se utilizará para la coincidencia de trabajos.",
                'fr': "Cette information sera utilisée pour la correspondance d'emploi.",
                'de': "Diese Information wird für Job-Matching verwendet.",
                'it': "Questa informazione sarà utilizzata per l'abbinamento di lavoro.",
                'pt': "Esta informação será usada para correspondência de emprego.",
                'am': "ይህ መረጃ ለሥራ ማዛመድ ጥቅም ላይ ይውላል።"
            }.get(lang_code, "This information will be used for job matching.")
            
            # Save profile to API
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await update.message.reply_text(
                f"{title}\n\n"
                f"{updated_to}\n\n"
                f"{note}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except ValueError:
            # Invalid input error messages
            error_title = {
                'en': "❌ *Invalid Input*",
                'es': "❌ *Entrada Inválida*",
                'fr': "❌ *Entrée Invalide*",
                'de': "❌ *Ungültige Eingabe*",
                'it': "❌ *Input Non Valido*",
                'pt': "❌ *Entrada Inválida*",
                'am': "❌ *ልክ ያልሆነ ግብዓት*"
            }.get(lang_code, "❌ *Invalid Input*")
            
            error_msg = {
                'en': "Please enter your age as a number.",
                'es': "Por favor, introduce tu edad como un número.",
                'fr': "Veuillez entrer votre âge sous forme de nombre.",
                'de': "Bitte geben Sie Ihr Alter als Zahl ein.",
                'it': "Inserisci la tua età come numero.",
                'pt': "Por favor, insira sua idade como um número.",
                'am': "እባክዎ እድሜዎን እንደ ቁጥር ያስገቡ።"
            }.get(lang_code, "Please enter your age as a number.")
            
            await update.message.reply_text(
                f"{error_title}\n\n"
                f"{error_msg}",
                parse_mode="Markdown"
            )

_shared_notif_prefs = {}



async def send_notification_settings(update, context):
    user_id = update.effective_user.id
    if user_id not in _shared_notif_prefs:
        _shared_notif_prefs[user_id] = {'job_alerts': True, 'application_updates': True, 'messages': True, 'marketing': False}
    prefs = _shared_notif_prefs[user_id]
    keyboard = [
        [KeyboardButton(f"🚨 Job Alerts: {'ON' if prefs['job_alerts'] else 'OFF'}"),
         KeyboardButton(f"📄 App Updates: {'ON' if prefs['application_updates'] else 'OFF'}")],
        [KeyboardButton(f"💬 Messages: {'ON' if prefs['messages'] else 'OFF'}"),
         KeyboardButton(f"📢 Marketing: {'ON' if prefs['marketing'] else 'OFF'}")],
        [KeyboardButton("⬅️ Back to Account")]
    ]
    await update.effective_message.reply_text(
        "🔔 Notification Settings\n\n"
        "Manage your notification preferences:\n\n"
        "Tap a button below to toggle it.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def send_delete_confirmation(update, context):
    keyboard = [
        [KeyboardButton("⚠️ YES, DELETE MY ACCOUNT")],
        [KeyboardButton("❌ Cancel")]
    ]
    await update.effective_message.reply_text(
        "🗑️ Delete Account\n\n"
        "WARNING: This action is permanent and cannot be undone!\n\n"
        "What will be deleted:\n"
        "- Your profile information\n"
        "- Uploaded CV and documents\n"
        "- Job application history\n"
        "- All saved preferences\n\n"
        "Are you sure you want to permanently delete your account?\n\n"
        "Tap the button below to confirm.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def privacy_policy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Terms", callback_data="settings_terms")]
    ]
    
    privacy_text = (
        "🔒 Privacy Policy\n\n"
        "Last Updated: October 2024\n\n"
        "Your privacy is important to us. Here's how we protect your data:\n\n"
        "Data We Collect:\n"
        "- Basic profile information (name, username)\n"
        "- CVs and documents you upload\n"
        "- Job application history\n"
        "- Usage analytics (anonymous)\n\n"
        "How We Protect Your Data:\n"
        "- Encrypted storage of all personal information\n"
        "- Secure file handling for CVs and documents\n"
        "- No sharing of personal data with third parties\n"
        "- Regular security audits and updates\n\n"
        "How We Use Your Data:\n"
        "- Matching you with relevant job opportunities\n"
        "- Improving our service quality\n"
        "- Sending important notifications (if enabled)\n\n"
        "Your Rights:\n"
        "- Request data deletion at any time\n"
        "- Access your stored information\n"
        "- Opt-out of data processing\n\n"
        "Contact: @HustleXSupport for privacy questions"
    )
    
    await safe_edit_message(
        q,
        privacy_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        context=context
    )

# ---------------------------
# Profile API Integration
# ---------------------------
async def save_profile_to_api(user_id, profile_data):
    """Save user profile edits to MongoDB."""
    try:
        fields = {}
        if profile_data.get("custom_name"):
            fields["name"] = profile_data["custom_name"]
        if profile_data.get("age"):
            fields["age"] = profile_data["age"]
        if profile_data.get("sex"):
            fields["sex"] = profile_data["sex"]
        if profile_data.get("contact_info"):
            fields["contact_info"] = profile_data["contact_info"]
        if profile_data.get("profile_pic_file_id"):
            fields["profile_pic_file_id"] = profile_data["profile_pic_file_id"]
        if not fields:
            return True
        return save_profile_fields(user_id, fields)
    except Exception as e:
        logger.error(f"Error saving profile to API: {e}")
        return False

# ---------------------------
# File uploads handler (CV / profile picture)
# ---------------------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    user_id = update.effective_user.id
    
    # Check if we're waiting for a profile photo
    awaiting_photo = context.user_data.get('awaiting_input') == 'photo'
    
    if m.document:
        # Check if it's a CV file (PDF or DOCX)
        file_name = m.document.file_name or "document"
        file_size = m.document.file_size
        mime_type = m.document.mime_type
        
        # Validate file type
        if file_name.lower().endswith(('.pdf', '.docx')) or mime_type in ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
            # Download actual file bytes from Telegram
            try:
                tele_file = await context.bot.get_file(m.document.file_id)
                file_bytes = await tele_file.download_as_bytearray()
            except Exception as e:
                logger.error(f"Failed to download CV file: {e}")
                file_bytes = None
            
            # Save to MongoDB
            cv_fields = {
                "cv_file_data": file_bytes,
                "cv_filename": file_name,
                "cv_mime_type": mime_type,
                "cv_file_size": file_size,
                "cv_upload_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_profile_fields(user_id, cv_fields)
            
            # Also keep in-memory for fast access
            user_cvs[user_id] = {
                'file_id': m.document.file_id,
                'filename': file_name,
                'file_size': file_size,
                'mime_type': mime_type,
                'upload_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            keyboard = [
                [InlineKeyboardButton("👁️ View CV", callback_data="cv_view")],
                [InlineKeyboardButton("📄 My CV Settings", callback_data="settings_cv")]
            ]
            
            await m.reply_text(
                f"✅ *CV Upload Successful!*\n\n"
                f"📁 *File:* {file_name}\n"
                f"📏 *Size:* {file_size:,} bytes\n"
                f"📝 *Type:* {'PDF' if file_name.lower().endswith('.pdf') else 'Word Document'}\n\n"
                f"🎉 Your CV is now ready for job applications!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await m.reply_text(
                "❌ *Invalid File Type*\n\n"
                "Please upload a PDF (.pdf) or Word document (.docx) file.\n\n"
                "📝 *Supported formats:*\n"
                "• PDF (.pdf)\n"
                "• Word Document (.docx)"
            )
            
    elif m.photo:
        file_id = m.photo[-1].file_id
        
        # Initialize user profile if not exists
        if user_id not in user_profiles:
            user_profiles[user_id] = {}
        
        # Store the profile picture file_id
        user_profiles[user_id]['profile_pic_file_id'] = file_id
        
        # Clear the awaiting input flag if we were waiting for a photo
        if awaiting_photo:
            context.user_data.pop('awaiting_input', None)
            
            # Get user's language preference
            lang_code = user_languages.get(user_id, 'en')
            
            # Language-specific messages
            title = {
                'en': "✅ *Profile Photo Updated Successfully!*",
                'es': "✅ *¡Foto de Perfil Actualizada Exitosamente!*",
                'fr': "✅ *Photo de Profil Mise à Jour avec Succès!*",
                'de': "✅ *Profilbild Erfolgreich Aktualisiert!*",
                'it': "✅ *Foto Profilo Aggiornata con Successo!*",
                'pt': "✅ *Foto de Perfil Atualizada com Sucesso!*",
                'am': "✅ *የመገለጫ ፎቶ በተሳካ ሁኔታ ተዘምኗል!*"
            }.get(lang_code, "✅ *Profile Photo Updated Successfully!*")
            
            message = {
                'en': "Your new profile photo has been saved.",
                'es': "Tu nueva foto de perfil ha sido guardada.",
                'fr': "Votre nouvelle photo de profil a été enregistrée.",
                'de': "Ihr neues Profilbild wurde gespeichert.",
                'it': "La tua nuova foto profilo è stata salvata.",
                'pt': "Sua nova foto de perfil foi salva.",
                'am': "አዲሱ የመገለጫ ፎቶዎ ተቀምጧል።"
            }.get(lang_code, "Your new profile photo has been saved.")
            
            tip = {
                'en': "💡 *Tip:* A professional profile photo increases your chances of getting hired!",
                'es': "💡 *Consejo:* ¡Una foto de perfil profesional aumenta tus posibilidades de ser contratado!",
                'fr': "💡 *Conseil:* Une photo de profil professionnelle augmente vos chances d'être embauché !",
                'de': "💡 *Tipp:* Ein professionelles Profilbild erhöht Ihre Chancen, eingestellt zu werden!",
                'it': "💡 *Suggerimento:* Una foto profilo professionale aumenta le tue possibilità di essere assunto!",
                'pt': "💡 *Dica:* Uma foto de perfil profissional aumenta suas chances de ser contratado!",
                'am': "💡 *ምክር:* ሙያዊ የመገለጫ ፎቶ የመቀጠር እድልዎን ይጨምራል!"
            }.get(lang_code, "💡 *Tip:* A professional profile photo increases your chances of getting hired!")
            
            back_text = {
                'en': "⬅️ Back to Profile",
                'es': "⬅️ Volver al Perfil",
                'fr': "⬅️ Retour au Profil",
                'de': "⬅️ Zurück zum Profil",
                'it': "⬅️ Torna al Profilo",
                'pt': "⬅️ Voltar ao Perfil",
                'am': "⬅️ ወደ መገለጫ ይመለሱ"
            }.get(lang_code, "⬅️ Back to Profile")
            
            keyboard = [
                [InlineKeyboardButton(back_text, callback_data="account_edit_profile")]
            ]
            
            # Save profile to API
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await m.reply_text(
                f"{title}\n\n"
                f"{message}\n\n"
                f"{tip}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await m.reply_text(
                "📸 Profile picture received — saved.\n\n"
                "💡 *Tip:* You can manage your profile in Settings → Account.",
                parse_mode="Markdown"
            )

# ---------------------------
# Telegram Job Posting Handlers
# ---------------------------
async def post_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("📝 Enter Job Title:")
    else:
        await update.message.reply_text("📝 Enter Job Title:")
    return JOB_TITLE

async def job_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["job_title"] = update.message.text
    await update.message.reply_text("Enter Job Type (Full-time, Part-time, Freelance):")
    return JOB_TYPE

async def job_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["job_type"] = update.message.text
    await update.message.reply_text("Enter Work Location:")
    return WORK_LOCATION

async def work_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["work_location"] = update.message.text
    await update.message.reply_text("Enter Salary:")
    return SALARY

async def salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["salary"] = update.message.text
    await update.message.reply_text("Enter Deadline (YYYY-MM-DD):")
    return DEADLINE

async def deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["deadline"] = update.message.text
    await update.message.reply_text("Enter Job Description:")
    return DESCRIPTION

async def description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text
    await update.message.reply_text("Enter Client Type (Private / Other):")
    return CLIENT_TYPE

async def client_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_type"] = update.message.text
    await update.message.reply_text("Enter Company Name:")
    return COMPANY_NAME

async def company_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["company_name"] = update.message.text
    await update.message.reply_text("Is the company verified? (✅ / No)")
    return VERIFIED

async def verified(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text not in ["yes", "no", "✅"]:
        await update.message.reply_text("Please reply with ✅ or No")
        return VERIFIED
    
    # Set the value to ✅ if the input is 'yes' or the checkmark symbol
    if text == "yes" or text == "✅":
        context.user_data["verified"] = "✅"
    else:
        context.user_data["verified"] = "No"
    await update.message.reply_text("List any previous jobs posted by this company (or type 'None'):")
    return PREVIOUS_JOBS

async def previous_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["previous_jobs"] = update.message.text
    await update.message.reply_text("Enter the link for 'View Details':")
    return JOB_LINK

async def job_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["job_link"] = update.message.text
    job_data = context.user_data

    job_id = save_job_to_db(job_data)
    apply_url = f"https://t.me/{BOT_USERNAME}?start=job_{job_id}"
    job_details_url = f"{WEBAPP_URL.rstrip('/')}/job-details/{job_id}"
    keyboard = [[InlineKeyboardButton("Apply Job", url=apply_url)]]

    # Check channel ID and bot permissions
    CHANNEL_ID = "-1003194542999"  # TODO: Replace with the correct channel ID
    if not await validate_channel_id(context, CHANNEL_ID):
        error_msg = (
            "❌ Error: Invalid channel ID. Please verify the CHANNEL_ID in the bot configuration.\n"
            "To find the correct ID, add the bot to the channel, send a message, and forward it to @userinfobot or @RawDataBot."
        )
        await update.message.reply_text(error_msg)
        logger.error(f"Job posting failed due to invalid CHANNEL_ID: {CHANNEL_ID}")
        return ConversationHandler.END

    if not await check_bot_permissions(context, CHANNEL_ID):
        error_msg = (
            "❌ Error: The bot does not have permission to post in the channel.\n"
            "Please make the bot an admin with 'Send Messages' permission."
        )
        await update.message.reply_text(error_msg)
        logger.error(f"Job posting failed due to insufficient permissions in channel: {CHANNEL_ID}")
        return ConversationHandler.END

    # Build job post with enhanced escaping
    job_text = (
        f"📢 *New Job Posted\\!* \n\n"
        f"*Job Title:* {escape_markdown_v2(job_data['job_title'])}\n"
        f"*Job Type:* {escape_markdown_v2(job_data['job_type'])}\n"
        f"*Location:* {escape_markdown_v2(job_data['work_location'])}\n"
        f"*Salary:* {escape_markdown_v2(job_data['salary'])}\n"
        f"*Deadline:* {escape_markdown_v2(job_data['deadline'])}\n"
        f"*Description:* {escape_markdown_v2(job_data['description'])}\n"
        f"*Client Type:* {escape_markdown_v2(job_data['client_type'])}\n"
        f"*Company Name:* {escape_markdown_v2(job_data['company_name'])}\n"
        f"*Verified:* {escape_markdown_v2('✅') if job_data['verified'].lower() == 'yes' else escape_markdown_v2(job_data['verified'])}\n"
        f"*Previous Jobs:* {escape_markdown_v2(job_data['previous_jobs'])}\n\n"
        f"From: HustleXet\\.com\n"
        f"@HustleXeth\n"
        f"@HustleXet\n"
        f"@HustleXet\\_bot"
    )

    try:
        sent_message = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=job_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        # Store the post in user_posts
        user_id = update.effective_user.id
        if user_id not in user_posts:
            user_posts[user_id] = []
        user_posts[user_id].append({
            "message_id": sent_message.message_id,
            "title": job_data["job_title"],
            "channel_id": CHANNEL_ID,
            "timestamp": datetime.now().isoformat()
        })
        await update.message.reply_text("✅ Job posted successfully!")
        logger.info(f"Job posted successfully to channel {CHANNEL_ID}")
    except TelegramError as e:
        error_msg = f"❌ Failed to post job: {str(e)}"
        logger.error(f"Failed to post job to channel {CHANNEL_ID}: {e}")
        if "chat not found" in str(e).lower():
            error_msg += "\nThe channel ID is incorrect or the bot is not a member of the channel."
        elif "not found" in str(e).lower():
            error_msg += "\nThe Telegram API returned a 404 error. This could be due to an invalid channel ID or bot token."
        elif "Bad Request: can't parse" in str(e):
            error_msg += "\nInvalid characters in job details. Ensure all fields are properly formatted."
        elif "Too Many Requests" in str(e):
            error_msg += "\nTelegram rate limit exceeded. Please try again later."
        await update.message.reply_text(error_msg)
        return ConversationHandler.END

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Job posting cancelled.")
    return ConversationHandler.END

# ---------------------------
# Main Function
# ---------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and handle them gracefully"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # Handle specific error types
    if "Message to edit not found" in str(context.error):
        # This error is already handled by safe_edit_message, just log it
        logger.info("Message edit failed - message not found")
    elif update and hasattr(update, 'effective_chat') and update.effective_chat:
        # Send a generic error message to the user
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Something went wrong. Please try again or use /start to restart."
            )
        except Exception:
            pass  # If we can't even send a message, just log it



async def post_registration_to_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str = "") -> bool:
    """Post a registration announcement to @HustleXeth when a user registers in the bot."""
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003194542999")
    contact = f"@{username}" if username else f"User {user_id}"
    announcement = (
        f"🎉 New Freelancer Registered!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 {contact} has joined HustleX!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"HustleX — Elite Freelancers Worldwide\n"
        f"@HustleXet_bot"
    )
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=announcement)
        logger.info(f"Registration announcement posted to channel for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to post registration announcement: {e}")
        return False


async def handle_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When a user joins @HustleXeth, auto-register them in the bot."""
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003194542999")
    chat_member = update.chat_member
    if not chat_member:
        return
    chat_id = str(chat_member.chat.id)
    if chat_id != str(CHANNEL_ID) and chat_id != CHANNEL_ID:
        return

    old_status = chat_member.old_chat_member.status if chat_member.old_chat_member else None
    new_status = chat_member.new_chat_member.status if chat_member.new_chat_member else None

    if new_status == "member" and old_status in ("left", "kicked", None):
        user = chat_member.new_chat_member.user
        user_id = user.id
        username = user.username or ""
        first_name = user.first_name or ""

        if is_user_registered(user_id):
            logger.info(f"User {user_id} already registered, skipping channel join registration")
            return

        success = register_user(user_id, username, first_name)
        if success:
            logger.info(f"User {user_id} auto-registered from channel join")
            await post_registration_to_channel(context, user_id, username)


async def handle_channel_profile_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor channel for profile card messages and sync back to MongoDB."""
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003194542999")
    if not update.channel_post or not update.channel_post.text:
        return

    chat_id = str(update.channel_post.chat.id)
    if chat_id != str(CHANNEL_ID) and chat_id != CHANNEL_ID:
        return

    text = update.channel_post.text

    # Only process messages matching our profile card format
    if not text.startswith("🆕 New Freelancer Profile!"):
        return

    try:
        lines = text.strip().split("\n")
        name = ""
        age = ""
        sex = ""
        contact = ""

        for line in lines:
            line = line.strip()
            if line.startswith("👤 Name:"):
                name = line.replace("👤 Name:", "").strip()
            elif line.startswith("🎂 Age:"):
                age = line.replace("🎂 Age:", "").strip()
            elif line.startswith("⚧ Gender:"):
                sex = line.replace("⚧ Gender:", "").strip()
            elif line.startswith("📱 Contact:"):
                contact = line.replace("📱 Contact:", "").strip()

        if not name or name == "Not set":
            return

        username = contact.lstrip("@") if contact != "N/A" else ""

        if not username:
            return

        database = get_db()
        if not database:
            return

        user_info = database.registered_users.find_one({"username": username})
        if not user_info:
            logger.info(f"No registered user found for @{username} from channel profile card")
            return

        user_id = user_info["user_id"]
        existing = database.profiles.find_one({"user_id": user_id})
        if not existing:
            return

        updates = {}
        if name and name != "Not set":
            updates["name"] = name
        if age and age != "N/A":
            try:
                updates["age"] = int(age)
            except ValueError:
                pass
        if sex and sex != "N/A":
            updates["sex"] = sex

        if updates:
            updates["synced_from_channel_at"] = datetime.utcnow()
            database.profiles.update_one(
                {"user_id": user_id},
                {"$set": updates},
            )
            logger.info(f"Profile synced from channel for user {user_id}")
    except Exception as e:
        logger.error(f"Error processing channel profile message: {e}")


def main():
    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("start", "Main Menu"),
        ])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    # Add error handler
    app.add_error_handler(error_handler)

    async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Show profile URL as clickable link
        profile_url = "https://hustlexet.vercel.app/freelancer-profile-setup"
        await update.effective_message.reply_text(
            f"👤 *Profile*\n\nClick here to access your profile: {profile_url}",
            parse_mode="Markdown"
        )

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register_complete", register_complete))

    # Contact handler for phone number sharing
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # CallbackQuery handlers — MUST be BEFORE ConversationHandler
    # because ConversationHandler's MessageHandler states consume callback updates
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(applications_cb, pattern="^applications$"))
    app.add_handler(CallbackQueryHandler(about_cb, pattern="^about$"))
    app.add_handler(CallbackQueryHandler(settings_cb, pattern="^settings$"))

    # Settings tab handlers
    app.add_handler(CallbackQueryHandler(settings_languages_cb, pattern="^settings_languages$"))
    app.add_handler(CallbackQueryHandler(settings_account_cb, pattern="^settings_account$"))
    app.add_handler(CallbackQueryHandler(settings_cv_cb, pattern="^settings_cv$"))
    app.add_handler(CallbackQueryHandler(settings_terms_cb, pattern="^settings_terms$"))

    # Language selection handlers
    app.add_handler(CallbackQueryHandler(language_selection, pattern="^lang_"))

    # CV action handlers
    app.add_handler(CallbackQueryHandler(cv_upload_handler, pattern="^cv_upload$"))
    app.add_handler(CallbackQueryHandler(cv_view_handler, pattern="^cv_view$"))
    app.add_handler(CallbackQueryHandler(cv_remove_handler, pattern="^cv_remove$"))

    # Account management handlers
    app.add_handler(CallbackQueryHandler(account_edit_profile_handler, pattern="^account_edit_profile$"))
    app.add_handler(CallbackQueryHandler(privacy_policy_handler, pattern="^terms_privacy$"))

    # Channel member update handler (auto-register when user joins @HustleXeth)
    app.add_handler(ChatMemberHandler(
        handle_channel_member_update, chat_member_types=ChatMemberHandler.CHAT_MEMBER
    ))

    # Job Posting ConversationHandler — AFTER all CallbackQueryHandlers
    job_post_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(post_job_start, pattern="^post_job_telegram$"),
                      CommandHandler("postjob", post_job_start),
                      MessageHandler(filters.Regex(r'^Post Job in Telegram$') | filters.Regex(r'^Publicar Trabajo en Telegram$') | filters.Regex(r'^Publier un Emploi sur Telegram$') | filters.Regex(r'^Stelle in Telegram veröffentlichen$') | filters.Regex(r'^Pubblica Lavoro su Telegram$') | filters.Regex(r'^Publicar Emprego no Telegram$') | filters.Regex(r'^ሥራን በቴሌግራም ያስቀምጡ$'), post_job_start)],
        states={
            JOB_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_title)],
            JOB_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_type)],
            WORK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, work_location)],
            SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, salary)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, deadline)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description)],
            CLIENT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, client_type)],
            COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, company_name)],
            VERIFIED: [MessageHandler(filters.TEXT & ~filters.COMMAND, verified)],
            PREVIOUS_JOBS: [MessageHandler(filters.TEXT & ~filters.COMMAND, previous_jobs)],
            JOB_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True
    )
    app.add_handler(job_post_conv)

    # File/message handlers
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, file_handler))

    # Web app data handler
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    # Text menu handler — must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Channel message handler (profile card sync) — runs after everything else
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=os.getenv("CHANNEL_ID", "-1003194542999")) & filters.TEXT,
        handle_channel_profile_message,
    ), group=1)

    # Run bot
    app.run_polling()

if __name__ == "__main__":
    main()

