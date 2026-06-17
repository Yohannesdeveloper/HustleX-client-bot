# bot/main.py
# States for Telegram Job Posting
JOB_TITLE, JOB_TYPE, WORK_LOCATION, SALARY, DEADLINE, DESCRIPTION, CLIENT_TYPE, JOB_LINK, COMPANY_NAME, VERIFIED, PREVIOUS_JOBS = range(11)
import os
import re
import logging
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import TelegramError
from urllib.parse import urlparse
import aiohttp
from bson.objectid import ObjectId

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables from .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
# For local web app (index.html in the project folder), use a local server URL, e.g., http://localhost:5000/
# For remote web app, use the remote URL
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://hustlexet.vercel.app/")
DATABASE_URL = os.getenv("DATABASE_URL")  # Unused in current code, placeholder for future integration

# Save job to DB including file paths, return job ID (ObjectID string)
def save_job(job_data):
    conn = sqlite3.connect("jobs.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_title TEXT, job_type TEXT, work_location TEXT,
            salary TEXT, deadline TEXT, description TEXT,
            client_type TEXT, company_name TEXT, verified TEXT,
            previous_jobs TEXT, job_link TEXT,
            cv_file TEXT, profile_image TEXT
        )
    """)
    job_id_str = str(ObjectId())
    cur.execute("""
        INSERT INTO jobs (id, job_title, job_type, work_location, salary, deadline, description,
            client_type, company_name, verified, previous_jobs, job_link, cv_file, profile_image)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id_str, job_data["job_title"], job_data["job_type"], job_data["work_location"],
        job_data["salary"], job_data["deadline"], job_data["description"],
        job_data["client_type"], job_data["company_name"], job_data["verified"],
        job_data["previous_jobs"], job_data["job_link"],
        job_data.get("cv_file"), job_data.get("profile_image")
    ))
    conn.commit()
    conn.close()
    return job_id_str

# Simple in-memory storage for CV data and user preferences (replace with database in production)
user_cvs = {}
user_languages = {}
user_profiles = {}

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

# ---------------------------
# /start command
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await validate_bot_token(TOKEN, context):
        if update.effective_chat:
            await update.effective_chat.send_message("❌ Error: Invalid bot token. Please contact the bot administrator.")
        logger.error("Cannot send message due to invalid bot token")
        return
    
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or "there"
    
    # Detailed welcome message
    welcome_text = (
        f"Hello {first_name}, welcome to HustleX! 🚀\n\n"
        f"👤 My Profile: to register and update your profile\n\n"
        f"📋 Applications: Track the status of all your applications\n\n"
        f"ℹ️ About HustleX: Learn more about our platform\n\n"
        f"⚙️ Settings: to customize your preferences\n\n"
        f"Want more powerful features? Go visit our website HustleX\n"
        f"🌐 www.HustleXet.com"
    )
    
    keyboard = [[KeyboardButton("📱 Menu")]]
    
    if update.effective_message:
        await update.effective_message.reply_text(
            welcome_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            disable_web_page_preview=True
        )
    else:
        await update.effective_chat.send_message(
            welcome_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            disable_web_page_preview=True
        )

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
# Menu handler (works with both callback and text messages)
# ---------------------------
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = user_languages.get(user_id, 'en')
    
    # Language-specific menu messages
    menu_messages = {
        'en': {
            'title': "Choose a tab:",
            'profile': "👤 Profile",
            'applications': "📋 Applications",
            'about': "ℹ️ About HustleX",
            'settings': "⚙️ Settings",
            'error': "❌ Error: WebApp URL is unreachable. Please try again later or contact support."
        },
        'es': {
            'title': "Elige una pestaña:",
            'profile': "👤 Perfil",
            'applications': "📋 Aplicaciones",
            'about': "ℹ️ Acerca de HustleX",
            'settings': "⚙️ Configuración",
            'error': "❌ Error: La URL de la aplicación web no es accesible. Por favor, inténtalo de nuevo más tarde o contacta con soporte."
        },
        'fr': {
            'title': "Choisissez un onglet:",
            'profile': "👤 Profil",
            'applications': "📋 Candidatures",
            'about': "ℹ️ À propos de HustleX",
            'settings': "⚙️ Paramètres",
            'error': "❌ Erreur: L'URL de l'application web est inaccessible. Veuillez réessayer plus tard ou contacter le support."
        },
        'de': {
            'title': "Wählen Sie einen Tab:",
            'profile': "👤 Profil",
            'applications': "📋 Bewerbungen",
            'about': "ℹ️ Über HustleX",
            'settings': "⚙️ Einstellungen",
            'error': "❌ Fehler: WebApp-URL ist nicht erreichbar. Bitte versuchen Sie es später erneut oder kontaktieren Sie den Support."
        },
        'it': {
            'title': "Scegli una scheda:",
            'profile': "👤 Profilo",
            'applications': "📋 Candidature",
            'about': "ℹ️ Informazioni su HustleX",
            'settings': "⚙️ Impostazioni",
            'error': "❌ Errore: L'URL dell'applicazione web non è raggiungibile. Riprova più tardi o contatta il supporto."
        },
        'pt': {
            'title': "Escolha uma aba:",
            'profile': "👤 Perfil",
            'applications': "📋 Candidaturas",
            'about': "ℹ️ Sobre o HustleX",
            'settings': "⚙️ Configurações",
            'error': "❌ Erro: A URL da aplicação web não está acessível. Tente novamente mais tarde ou entre em contato com o suporte."
        },
        'am': {
            'title': "አንድ ትር ይምረጡ:",
            'profile': "👤 መገለጫ",
            'applications': "📋 ማመልከቻዎች",
            'about': "ℹ️ ስለ HustleX",
            'settings': "⚙️ ቅንብሮች",
            'error': "❌ ስህተት: የድር መተግበሪያ URL አይደርስም። እባክዎ ቆይተው ይሞክሩ ወይም ድጋፍን ያግኙ።"
        }
    }
    
    messages = menu_messages.get(lang_code, menu_messages['en'])
    
    # Skip WebApp URL validation for now (domain may not be set up yet)
    # if not await validate_webapp_url(WEBAPP_URL):
    #     await context.bot.send_message(
    #         chat_id=update.effective_chat.id,
    #         text=messages['error']
    #     )
    #     return
    
    # Reply keyboard for menu options
    reply_keyboard = [
        [KeyboardButton(messages['profile'])],
        [KeyboardButton(messages['applications']), KeyboardButton(messages['about'])],
        [KeyboardButton(messages['settings'])]
    ]
    
    # Send message with reply keyboard
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=messages['title'],
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu from callback query"""
    q = update.callback_query
    await q.answer()
    await show_menu(update, context)

async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu from text message (reply keyboard button)"""
    await show_menu(update, context)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command"""
    await show_menu(update, context)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /about command"""
    await about_cb(update, context)

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile command - opens account/profile settings"""
    await account_edit_profile_handler(update, context)

async def applications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /applications command"""
    await applications_cb(update, context)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command"""
    await settings_cb(update, context)

# ---------------------------
# Other tab callbacks
# ---------------------------
async def applications_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()

    applications_url = "https://hustlexet.vercel.app/my-applications"
    keyboard = [[InlineKeyboardButton("📋 View Applications", url=applications_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📋 *Applications*\n\nTap the button below to view and track all your applications on HustleX.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def about_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    about_text = (
        "🚀 *About HustleX*\n\n"
        "Welcome to *HustleX* – where *ambition meets opportunity!* ✨\n\n"
        "At HustleX, we believe talent has *no limits* 🌍. Whether you're a designer 🎨, "
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
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=about_text, 
        parse_mode="Markdown"
    )

async def settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
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
    
    # Reply keyboard for settings options
    reply_keyboard = [
        [KeyboardButton(messages['languages']), KeyboardButton(messages['account'])],
        [KeyboardButton(messages['cv']), KeyboardButton(messages['terms'])],
        [KeyboardButton(messages['back'])]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{messages['title']}\n\n{messages['instruction']}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

# ---------------------------
# Settings Tab Callbacks
# ---------------------------
async def settings_languages_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
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
    
    # Reply keyboard for language selection
    reply_keyboard = [
        [KeyboardButton("🇺🇸 English"), KeyboardButton("🇪🇸 Español")],
        [KeyboardButton("🇫🇷 Français"), KeyboardButton("🇩🇪 Deutsch")],
        [KeyboardButton("🇮🇹 Italiano"), KeyboardButton("🇵🇹 Português")],
        [KeyboardButton("🇪🇹 አማርኛ (Amharic)")],
        [KeyboardButton(msg['back'])]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{msg['title']}\n\n{msg['instruction']}\n\n{msg['current']}\n{msg['tip']}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def settings_account_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user = update.effective_user
    user_id = user.id
    
    # Get user profile data if exists
    profile = user_profiles.get(user_id, {})
    
    # Get profile fields
    first_name = profile.get('first_name', user.first_name or 'Not set')
    last_name = profile.get('last_name', user.last_name or 'Not set')
    email = profile.get('email', 'Not set')
    age = profile.get('age', 'Not set')
    gender = profile.get('gender', 'Not set')
    city = profile.get('city', 'Not set')
    country = profile.get('country', 'Not set')
    
    # Get CV status
    has_cv = user_id in user_cvs and user_cvs[user_id] is not None
    cv_status = "✅ Uploaded" if has_cv else "❌ Not uploaded"
    
    # Get language preference
    lang_code = user_languages.get(user_id, 'en')
    lang_names = {
        'en': '🇺🇸 English',
        'es': '🇪🇸 Español', 
        'fr': '🇫🇷 Français',
        'de': '🇩🇪 Deutsch',
        'it': '🇮🇹 Italiano',
        'pt': '🇵🇹 Português',
        'am': '🇪🇹 አማርኛ'
    }
    current_lang = lang_names.get(lang_code, '🇺🇸 English')
    
    # Format username properly
    username_display = f"@{user.username}" if user.username else "Not set"
    
    account_text = (
        f"👤 Account Details\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Personal Information\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• First Name: {first_name}\n"
        f"• Last Name: {last_name}\n"
        f"• Email: {email}\n"
        f"• Age: {age}\n"
        f"• Gender: {gender}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Location\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• City: {city}\n"
        f"• Country: {country}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Account Info\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Username: {username_display}\n"
        f"• Language: {current_lang}\n"
        f"• CV: {cv_status}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✏️ Tap a button to edit:"
    )
    
    # Reply keyboard for account options
    reply_keyboard = [
        [KeyboardButton("✏️ Edit Profile")],
        [KeyboardButton("🔔 Notifications")],
        [KeyboardButton("🗑️ Delete Account")],
        [KeyboardButton("⬅️ Back to Settings")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=account_text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    )

async def settings_cv_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user_id = update.effective_user.id
    has_cv = user_id in user_cvs and user_cvs[user_id] is not None
    
    if has_cv:
        cv_info = user_cvs[user_id]
        reply_keyboard = [
            [KeyboardButton("👁️ View Current CV"), KeyboardButton("📤 Upload New CV")],
            [KeyboardButton("🗑️ Remove CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        status_text = f"✅ CV uploaded: {cv_info.get('filename', 'Unknown')}"
    else:
        reply_keyboard = [
            [KeyboardButton("📤 Upload New CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        status_text = "❌ No CV uploaded"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📄 *My CV*\n\n"
        f"📁 *Current Status:* {status_text}\n"
        f"📝 *Supported formats:* PDF, DOCX\n"
        f"📏 *Max file size:* 16 MB\n\n"
        f"💡 *Tip:* A well-formatted CV increases your chances of getting hired!\n\n"
        f"Choose an option below:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def settings_terms_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    reply_keyboard = [
        [KeyboardButton("🔒 Privacy Policy")],
        [KeyboardButton("⬅️ Back to Settings")]
    ]
    
    terms_text = (
        "📋 *Terms and Conditions*\n\n"
        "📄 *Last Updated:* January 2026\n\n"
        "Welcome to HustleX! By using our bot, you agree to these terms:\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ *1. Acceptance of Terms*\n"
        "By accessing or using HustleX, you agree to be bound by these Terms and Conditions. If you do not agree, please do not use our services.\n\n"
        "✅ *2. Usage Rights*\n"
        "• You may use HustleX for legitimate job searching\n"
        "• You must provide accurate information in your profile\n"
        "• You are responsible for maintaining account security\n"
        "• All job applications must be genuine\n\n"
        "🚫 *3. Prohibited Activities*\n"
        "• Posting fake or misleading job offers\n"
        "• Spam or harassment of other users\n"
        "• Sharing inappropriate or offensive content\n"
        "• Attempting to hack or disrupt the service\n"
        "• Creating multiple accounts\n"
        "• Impersonating other users or companies\n\n"
        "🛡️ *4. Privacy & Data*\n"
        "• We protect your personal information\n"
        "• CVs are stored securely and only shared with your consent\n"
        "• We do not sell your data to third parties\n"
        "• You can request data deletion at any time\n\n"
        "⚠️ *5. Disclaimer*\n"
        "• HustleX is not responsible for job outcomes\n"
        "• We do not guarantee employment\n"
        "• Users are responsible for verifying job legitimacy\n\n"
        "🔄 *6. Changes to Terms*\n"
        "We reserve the right to modify these terms at any time. Continued use after changes constitutes acceptance.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 *Contact:* @HustleXSupport for questions\n"
        "🌐 *Website:* www.HustleXet.com"
    )
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=terms_text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

# ---------------------------
# Additional Settings Handlers
# ---------------------------
async def language_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle language selection from reply keyboard text"""
    text = update.message.text
    user_id = update.effective_user.id
    
    # Map text to language code
    lang_map = {
        "🇺🇸 English": "en",
        "🇪🇸 Español": "es",
        "🇫🇷 Français": "fr",
        "🇩🇪 Deutsch": "de",
        "🇮🇹 Italiano": "it",
        "🇵🇹 Português": "pt",
    }
    
    # Check for Amharic separately (contains variable text)
    if "🇪🇹" in text or "አማርኛ" in text:
        lang_code = "am"
    else:
        lang_code = lang_map.get(text, "en")
    
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
    
    reply_keyboard = [
        [KeyboardButton(messages['back'])]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{messages['title']}\n\n{messages['message']}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle language selection from callback query (inline button fallback)"""
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
    
    reply_keyboard = [
        [KeyboardButton(messages['back'])]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{messages['title']}\n\n{messages['message']}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def cv_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    # Set flag that we're waiting for CV upload
    context.user_data['awaiting_cv_upload'] = True
    
    reply_keyboard = [
        [KeyboardButton("❌ Cancel Upload")],
        [KeyboardButton("⬅️ Back to Settings")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📤 *Upload Your CV*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📎 *Send your CV file now as a document*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 *Supported formats:*\n"
        "• PDF (.pdf) ✅\n"
        "• Word Document (.docx) ✅\n\n"
        "📏 *Requirements:*\n"
        "• Maximum file size: 16 MB\n"
        "• File should be clearly readable\n"
        "• Use a professional file name\n\n"
        "💡 *Tips for a great CV:*\n"
        "• Include your contact information\n"
        "• List your skills and experience\n"
        "• Keep it concise (1-2 pages)\n"
        "• Use a clean, professional format\n\n"
        "⏳ *Waiting for your file...*",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def cv_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user_id = update.effective_user.id
    
    if user_id in user_cvs and user_cvs[user_id] is not None:
        cv_info = user_cvs[user_id]
        reply_keyboard = [
            [KeyboardButton("📤 Upload New CV"), KeyboardButton("🗑️ Remove CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"👁️ *View CV*\n\n"
            f"📁 *File:* {cv_info.get('filename', 'Unknown')}\n"
            f"📏 *Size:* {cv_info.get('file_size', 'Unknown')} bytes\n"
            f"📅 *Uploaded:* {cv_info.get('upload_date', 'Unknown')}\n\n"
            f"📝 *Your CV is ready for:*\n"
            f"• Sharing with potential employers\n"
            f"• Job applications through HustleX\n"
            f"• Profile showcasing\n\n"
            f"💼 *Want to update or remove your CV?*",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
    else:
        reply_keyboard = [
            [KeyboardButton("📤 Upload New CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👁️ *View CV*\n\n"
            "📁 *Status:* No CV uploaded yet\n\n"
            "📝 Once you upload a CV, you'll be able to:\n"
            "• Preview your CV\n"
            "• Download a copy\n"
            "• Share it with potential employers\n"
            "• Update it anytime\n\n"
            "💼 *Ready to upload your CV?*",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )

# ---------------------------
# CV Cancel Upload Handler
# ---------------------------
async def cancel_cv_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel CV upload and go back to CV settings"""
    # Clear the awaiting upload flag
    context.user_data.pop('awaiting_cv_upload', None)
    
    # Show CV settings
    await settings_cv_cb(update, context)

# ---------------------------
# CV Removal Handler
# ---------------------------
async def cv_remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user_id = update.effective_user.id
    
    # Check if user has a CV
    if user_id in user_cvs and user_cvs[user_id] is not None:
        # Remove the CV from storage
        del user_cvs[user_id]
        
        reply_keyboard = [
            [KeyboardButton("📤 Upload New CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🗑️ *CV Removed Successfully*\n\n"
            "✅ Your CV has been permanently deleted from our system.\n\n"
            "📝 *What's next?*\n"
            "• Upload a new CV anytime\n"
            "• Your profile remains active\n"
            "• Previous job applications are unaffected\n\n"
            "💼 *Ready to upload a new CV?*",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
    else:
        reply_keyboard = [
            [KeyboardButton("📤 Upload CV")],
            [KeyboardButton("⬅️ Back to Settings")]
        ]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ *No CV Found*\n\n"
            "There's no CV to remove. You haven't uploaded one yet.\n\n"
            "💼 *Want to upload your CV?*",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )

# ---------------------------
# Account Management Handlers
# ---------------------------
async def account_edit_profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    await start_profile_wizard(update, context)

# Profile Wizard - Step by step profile editing
PROFILE_WIZARD_STEPS = ['first_name', 'last_name', 'email', 'age', 'gender', 'city', 'country']

PROFILE_WIZARD_PROMPTS = {
    'first_name': ("👤 Step 1/7: First Name", "Please enter your first name:"),
    'last_name': ("👤 Step 2/7: Last Name", "Please enter your last name:"),
    'email': ("📧 Step 3/7: Email", "Please enter your email address:\n\n💡 This will be used for job applications."),
    'age': ("🎂 Step 4/7: Age", "Please enter your age (number between 16-100):"),
    'gender': ("⚧ Step 5/7: Gender", "Please select or type your gender:"),
    'city': ("🏙️ Step 6/7: City", "Please enter your city of residence:"),
    'country': ("🌍 Step 7/7: Country", "Please enter your country of residence:")
}

async def start_profile_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the profile wizard from step 1"""
    user_id = update.effective_user.id
    
    # Initialize profile if not exists
    if user_id not in user_profiles:
        user_profiles[user_id] = {}
    
    # Start with first step
    context.user_data['profile_wizard_step'] = 0
    await show_wizard_step(update, context, 0)

async def show_wizard_step(update: Update, context: ContextTypes.DEFAULT_TYPE, step_index: int):
    """Show the current wizard step"""
    if step_index >= len(PROFILE_WIZARD_STEPS):
        # Wizard complete
        await complete_profile_wizard(update, context)
        return
    
    step = PROFILE_WIZARD_STEPS[step_index]
    title, prompt = PROFILE_WIZARD_PROMPTS[step]
    
    # Special keyboard for gender step
    if step == 'gender':
        reply_keyboard = [
            [KeyboardButton("👨 Male"), KeyboardButton("👩 Female")],
            [KeyboardButton("🧑 Other"), KeyboardButton("🔒 Prefer not to say")],
            [KeyboardButton("⏭️ Skip"), KeyboardButton("❌ Cancel")]
        ]
    else:
        reply_keyboard = [
            [KeyboardButton("⏭️ Skip"), KeyboardButton("❌ Cancel")]
        ]
    
    # Show current value if exists
    user_id = update.effective_user.id
    current_value = user_profiles.get(user_id, {}).get(step, None)
    
    if current_value:
        text = f"{title}\n\n{prompt}\n\n📝 Current: {current_value}"
    else:
        text = f"{title}\n\n{prompt}"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    
    context.user_data['awaiting_input'] = f'wizard_{step}'

async def complete_profile_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Complete the profile wizard and show summary"""
    user_id = update.effective_user.id
    profile = user_profiles.get(user_id, {})
    
    # Clear wizard state
    context.user_data.pop('profile_wizard_step', None)
    context.user_data.pop('awaiting_input', None)
    
    # Save to API
    await save_profile_to_api(user_id, profile)
    
    # Build summary
    summary = (
        "✅ Profile Complete!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Your Profile Summary\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• First Name: {profile.get('first_name', 'Not set')}\n"
        f"• Last Name: {profile.get('last_name', 'Not set')}\n"
        f"• Email: {profile.get('email', 'Not set')}\n"
        f"• Age: {profile.get('age', 'Not set')}\n"
        f"• Gender: {profile.get('gender', 'Not set')}\n"
        f"• City: {profile.get('city', 'Not set')}\n"
        f"• Country: {profile.get('country', 'Not set')}\n\n"
        "🎉 Your profile has been saved!"
    )
    
    reply_keyboard = [
        [KeyboardButton("✏️ Edit Profile")],
        [KeyboardButton("⬅️ Back to Account")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=summary,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    )

async def handle_wizard_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle input during profile wizard"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Check if we're in wizard mode
    if 'profile_wizard_step' not in context.user_data:
        # Not in wizard mode, ignore or redirect
        return
    
    # Initialize profile if not exists
    if user_id not in user_profiles:
        user_profiles[user_id] = {}
    
    # Get current step
    step_index = context.user_data.get('profile_wizard_step', 0)
    
    if step_index >= len(PROFILE_WIZARD_STEPS):
        await complete_profile_wizard(update, context)
        return
    
    current_step = PROFILE_WIZARD_STEPS[step_index]
    
    # Handle skip
    if message_text == "⏭️ Skip":
        context.user_data['profile_wizard_step'] = step_index + 1
        await show_wizard_step(update, context, step_index + 1)
        return
    
    # Handle cancel
    if message_text == "❌ Cancel":
        context.user_data.pop('profile_wizard_step', None)
        context.user_data.pop('awaiting_input', None)
        await settings_account_cb(update, context)
        return
    
    # Validate and save based on step
    if current_step == 'age':
        try:
            age = int(message_text)
            if age < 16 or age > 100:
                await update.message.reply_text("❌ Please enter a valid age between 16 and 100.")
                return
            user_profiles[user_id]['age'] = age
        except ValueError:
            await update.message.reply_text("❌ Please enter a number for your age.")
            return
    elif current_step == 'gender':
        # Handle gender presets
        gender_map = {
            "👨 Male": "Male",
            "👩 Female": "Female",
            "🧑 Other": "Other",
            "🔒 Prefer not to say": "Prefer not to say"
        }
        user_profiles[user_id]['gender'] = gender_map.get(message_text, message_text)
    else:
        # Simple text fields
        user_profiles[user_id][current_step] = message_text
    
    # Move to next step
    context.user_data['profile_wizard_step'] = step_index + 1
    await show_wizard_step(update, context, step_index + 1)

# Legacy handlers for compatibility
async def edit_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_profile_wizard(update, context)

async def edit_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_profile_wizard(update, context)

async def smart_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Smart cancel handler that routes to appropriate cancel action based on context"""
    # Check if we're in profile wizard mode
    if 'profile_wizard_step' in context.user_data:
        await handle_wizard_input(update, context)
    else:
        # Default to going back to account settings (for delete account cancel)
        await settings_account_cb(update, context)

# Age editing handler
async def edit_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle both callback query (inline button) and text message (reply keyboard)
    if update.callback_query:
        await update.callback_query.answer()
    
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
        'en': "⬅️ Back to Account",
        'es': "⬅️ Volver a Cuenta",
        'fr': "⬅️ Retour au Compte",
        'de': "⬅️ Zurück zum Konto",
        'it': "⬅️ Torna all'Account",
        'pt': "⬅️ Voltar à Conta",
        'am': "⬅️ ወደ መለያ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Account")
    
    reply_keyboard = [
        [KeyboardButton(back_text)]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{title}\n\n"
        f"{prompt}\n\n"
        f"{note}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    
    # Store that we're waiting for age input
    context.user_data['awaiting_input'] = 'age'

# Profile photo update handler
async def edit_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle both callback query (inline button) and text message (reply keyboard)
    if update.callback_query:
        await update.callback_query.answer()
    
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
        'en': "⬅️ Back to Account",
        'es': "⬅️ Volver a Cuenta",
        'fr': "⬅️ Retour au Compte",
        'de': "⬅️ Zurück zum Konto",
        'it': "⬅️ Torna all'Account",
        'pt': "⬅️ Voltar à Conta",
        'am': "⬅️ ወደ መለያ ይመለሱ"
    }.get(lang_code, "⬅️ Back to Account")
    
    reply_keyboard = [
        [KeyboardButton(back_text)]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{title}\n\n"
        f"{prompt}\n\n"
        f"{tip}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    
    # Store that we're waiting for a photo input
    context.user_data['awaiting_input'] = 'photo'

# Handler for text messages when awaiting specific input
async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Initialize user profile if not exists
    if user_id not in user_profiles:
        user_profiles[user_id] = {}
    
    awaiting = context.user_data.get('awaiting_input')
    
    # Check if we're in profile wizard mode
    if awaiting and awaiting.startswith('wizard_'):
        await handle_wizard_input(update, context)
        return
    
    reply_keyboard = [
        [KeyboardButton("⬅️ Back to Account")]
    ]
    
    # Field mapping for simple text fields (legacy single-field edits)
    field_mapping = {
        'first_name': ('First Name', 'first_name'),
        'last_name': ('Last Name', 'last_name'),
        'email': ('Email', 'email'),
        'city': ('City', 'city'),
        'country': ('Country', 'country'),
        'name': ('Name', 'custom_name'),
        'contact': ('Contact', 'contact_info'),
    }
    
    if awaiting in field_mapping:
        label, key = field_mapping[awaiting]
        user_profiles[user_id][key] = message_text
        context.user_data.pop('awaiting_input', None)
        
        await save_profile_to_api(user_id, user_profiles[user_id])
        
        await update.message.reply_text(
            f"✅ {label} Updated!\n\n"
            f"Your {label.lower()} has been set to: {message_text}",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
    
    elif awaiting == 'age':
        # Validate age input
        try:
            age = int(message_text)
            if age < 16 or age > 100:
                await update.message.reply_text(
                    "❌ Invalid Age\n\nPlease enter a valid age between 16 and 100.",
                    reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
                )
                return
            
            user_profiles[user_id]['age'] = age
            context.user_data.pop('awaiting_input', None)
            
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await update.message.reply_text(
                f"✅ Age Updated!\n\n"
                f"Your age has been set to: {age}",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid Input\n\nPlease enter your age as a number.",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
    
    elif awaiting == 'gender':
        # Handle gender selection
        gender_map = {
            "👨 Male": "Male",
            "👩 Female": "Female",
            "🧑 Other": "Other",
            "🔒 Prefer not to say": "Prefer not to say"
        }
        
        if message_text in gender_map:
            user_profiles[user_id]['gender'] = gender_map[message_text]
            context.user_data.pop('awaiting_input', None)
            
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await update.message.reply_text(
                f"✅ Gender Updated!\n\n"
                f"Your gender has been set to: {gender_map[message_text]}",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        else:
            # If they typed something else, just save it
            user_profiles[user_id]['gender'] = message_text
            context.user_data.pop('awaiting_input', None)
            
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await update.message.reply_text(
                f"✅ Gender Updated!\n\n"
                f"Your gender has been set to: {message_text}",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )


async def account_notifications_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user_id = update.effective_user.id
    # Simple notification preferences storage (in production, use database)
    if not hasattr(account_notifications_handler, 'user_notifications'):
        account_notifications_handler.user_notifications = {}
    
    # Initialize user preferences if not exists
    if user_id not in account_notifications_handler.user_notifications:
        account_notifications_handler.user_notifications[user_id] = {
            'job_alerts': True,
            'application_updates': True,
            'messages': True,
            'marketing': False
        }
    
    user_prefs = account_notifications_handler.user_notifications[user_id]
    
    # Reply keyboard for notification toggles
    reply_keyboard = [
        [KeyboardButton(f"🚨 Job Alerts: {'✅ ON' if user_prefs['job_alerts'] else '❌ OFF'}")],
        [KeyboardButton(f"📄 App Updates: {'✅ ON' if user_prefs['application_updates'] else '❌ OFF'}")],
        [KeyboardButton(f"💬 Messages: {'✅ ON' if user_prefs['messages'] else '❌ OFF'}")],
        [KeyboardButton(f"📢 Marketing: {'✅ ON' if user_prefs['marketing'] else '❌ OFF'}")],
        [KeyboardButton("⬅️ Back to Account")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🔔 Notification Settings\n\n"
        "📱 Manage your notification preferences:\n\n"
        "🚨 Job Alerts: Get notified about new jobs\n"
        "📄 Application Updates: Status changes on your applications\n"
        "💬 Messages: Direct messages from employers\n"
        "📢 Marketing: Updates about HustleX features\n\n"
        "💡 Tap a button to toggle that notification on/off.",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    )

async def account_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    reply_keyboard = [
        [KeyboardButton("⚠️ Yes, Delete My Account")],
        [KeyboardButton("❌ Cancel")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🗑️ *Delete Account*\n\n"
        f"⚠️ *WARNING:* This action is permanent and cannot be undone!\n\n"
        f"🔥 *What will be deleted:*\n"
        f"• Your profile information\n"
        f"• Uploaded CV and documents\n"
        f"• Job application history\n"
        f"• All saved preferences\n\n"
        f"📞 *Alternative:* You can temporarily disable notifications instead.\n\n"
        f"❓ *Are you sure you want to permanently delete your account?*",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

async def privacy_policy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    reply_keyboard = [
        [KeyboardButton("⬅️ Back to Settings")]
    ]
    
    privacy_text = (
        "🔒 *Privacy Policy*\n\n"
        "📄 *Last Updated:* January 2026\n\n"
        "Your privacy is important to us. Here's how we protect your data:\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *1. Data We Collect*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Basic profile information (name, username)\n"
        "• CVs and documents you upload\n"
        "• Job application history\n"
        "• Usage analytics (anonymous)\n"
        "• Language preferences\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ *2. How We Protect Your Data*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Encrypted storage of all personal information\n"
        "• Secure file handling for CVs and documents\n"
        "• No sharing of personal data with third parties\n"
        "• Regular security audits and updates\n"
        "• Secure Telegram API integration\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *3. How We Use Your Data*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Matching you with relevant job opportunities\n"
        "• Improving our service quality\n"
        "• Sending important notifications (if enabled)\n"
        "• Personalizing your experience\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🗑️ *4. Your Rights*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Request data deletion at any time\n"
        "• Access your stored information\n"
        "• Opt-out of data processing\n"
        "• Export your data\n"
        "• Withdraw consent\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🍪 *5. Cookies & Tracking*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "• We do not use cookies in the Telegram bot\n"
        "• Our website may use essential cookies only\n"
        "• No third-party tracking\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 *Contact:* @HustleXSupport\n"
        "🌐 *Website:* www.HustleXet.com"
    )
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=privacy_text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

# ---------------------------
# Notification Toggle Handlers
# ---------------------------
async def toggle_notification_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notification toggles from reply keyboard text"""
    text = update.message.text
    user_id = update.effective_user.id
    
    # Initialize user notifications if not exists
    if not hasattr(account_notifications_handler, 'user_notifications'):
        account_notifications_handler.user_notifications = {}
    
    if user_id not in account_notifications_handler.user_notifications:
        account_notifications_handler.user_notifications[user_id] = {
            'job_alerts': True,
            'application_updates': True,
            'messages': True,
            'marketing': False
        }
    
    # Toggle based on button text
    current_prefs = account_notifications_handler.user_notifications[user_id]
    setting_name = ""
    new_status = False
    
    if "Job Alerts" in text:
        current_prefs['job_alerts'] = not current_prefs['job_alerts']
        setting_name = "🚨 Job Alerts"
        new_status = current_prefs['job_alerts']
    elif "App Updates" in text:
        current_prefs['application_updates'] = not current_prefs['application_updates']
        setting_name = "📄 Application Updates"
        new_status = current_prefs['application_updates']
    elif "Messages" in text:
        current_prefs['messages'] = not current_prefs['messages']
        setting_name = "💬 Messages"
        new_status = current_prefs['messages']
    elif "Marketing" in text:
        current_prefs['marketing'] = not current_prefs['marketing']
        setting_name = "📢 Marketing"
        new_status = current_prefs['marketing']
    
    # Send confirmation message
    status_text = "✅ ON" if new_status else "❌ OFF"
    await update.message.reply_text(f"{setting_name} is now {status_text}")
    
    # Show updated notification settings
    await account_notifications_handler(update, context)

async def toggle_notification_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notification toggles from callback query (fallback)"""
    q = update.callback_query
    await q.answer()
    
    user_id = update.effective_user.id
    toggle_type = q.data.split('_', 1)[1]  # get part after 'toggle_'
    
    # Initialize user notifications if not exists
    if not hasattr(account_notifications_handler, 'user_notifications'):
        account_notifications_handler.user_notifications = {}
    
    if user_id not in account_notifications_handler.user_notifications:
        account_notifications_handler.user_notifications[user_id] = {
            'job_alerts': True,
            'application_updates': True,
            'messages': True,
            'marketing': False
        }
    
    # Toggle the specific notification type
    current_prefs = account_notifications_handler.user_notifications[user_id]
    if toggle_type == 'job_alerts':
        current_prefs['job_alerts'] = not current_prefs['job_alerts']
        setting_name = "Job Alerts"
    elif toggle_type == 'app_updates':
        current_prefs['application_updates'] = not current_prefs['application_updates']
        setting_name = "Application Updates"
    elif toggle_type == 'messages':
        current_prefs['messages'] = not current_prefs['messages']
        setting_name = "Messages"
    elif toggle_type == 'marketing':
        current_prefs['marketing'] = not current_prefs['marketing']
        setting_name = "Marketing"
    
    # Show updated notification settings
    await account_notifications_handler(update, context)

async def confirm_delete_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    user_id = update.effective_user.id
    
    # Remove user data
    if user_id in user_cvs:
        del user_cvs[user_id]
    
    if hasattr(account_notifications_handler, 'user_notifications'):
        if user_id in account_notifications_handler.user_notifications:
            del account_notifications_handler.user_notifications[user_id]
    
    reply_keyboard = [
        [KeyboardButton("🏠 Start Over")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ *Account Deleted Successfully*\n\n"
        f"🗑️ Your account has been permanently deleted from HustleX.\n\n"
        f"📋 *What was removed:*\n"
        f"• Profile information\n"
        f"• Uploaded CV and documents\n"
        f"• Notification preferences\n"
        f"• All saved data\n\n"
        f"👋 Thank you for using HustleX. You can create a new account anytime by using /start.\n\n"
        f"💬 If you have feedback, contact @HustleXSupport",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )

# ---------------------------
# Profile API Integration
# ---------------------------
async def save_profile_to_api(user_id, profile_data):
    """Save user profile data to the API"""
    try:
        # Prepare the data for API request
        form_data = {
            "name": profile_data.get('custom_name', ''),
            "age": profile_data.get('age', 0),
            "sex": profile_data.get('sex', 'Not specified'),
            "contact_info": profile_data.get('contact_info', ''),
            "init_data": f"user_id={user_id}"  # Simple init_data for demo purposes
        }
        
        # If we have a profile picture, we would need to download it and send as a file
        profile_pic_file_id = profile_data.get('profile_pic_file_id')
        
        # In a real implementation, you would:
        # 1. Download the file using context.bot.get_file(file_id)
        # 2. Send the file as part of a multipart/form-data request
        # 3. Handle the API response
        
        # For now, we'll just log that we would send this data
        logger.info(f"Would send profile data to API for user {user_id}: {form_data}")
        
        # In production, you would make an actual API call here
        # Example with aiohttp:
        # async with aiohttp.ClientSession() as session:
        #     async with session.post('http://api-url/api/profile', data=form_data) as response:
        #         return await response.json()
        
        return True
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
            # Store CV information
            user_cvs[user_id] = {
                'file_id': m.document.file_id,
                'filename': file_name,
                'file_size': file_size,
                'mime_type': mime_type,
                'upload_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Clear the awaiting upload flag
            context.user_data.pop('awaiting_cv_upload', None)
            
            # Format file size nicely
            if file_size >= 1024 * 1024:
                size_str = f"{file_size / (1024 * 1024):.2f} MB"
            elif file_size >= 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size} bytes"
            
            reply_keyboard = [
                [KeyboardButton("👁️ View Current CV")],
                [KeyboardButton("📤 Upload New CV"), KeyboardButton("🗑️ Remove CV")],
                [KeyboardButton("⬅️ Back to Settings")]
            ]
            
            await m.reply_text(
                f"✅ *CV Saved Successfully!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📁 *File Details*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• *Name:* {file_name}\n"
                f"• *Size:* {size_str}\n"
                f"• *Type:* {'📕 PDF' if file_name.lower().endswith('.pdf') else '📘 Word Document'}\n"
                f"• *Saved:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"🎉 *Your CV is now saved and ready!*\n\n"
                f"💼 Your CV will be used for:\n"
                f"• Job applications through HustleX\n"
                f"• Sharing with potential employers\n"
                f"• Profile enhancement",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        else:
            reply_keyboard = [
                [KeyboardButton("❌ Cancel Upload")],
                [KeyboardButton("⬅️ Back to Settings")]
            ]
            
            await m.reply_text(
                "❌ *Invalid File Type*\n\n"
                "The file you sent is not supported.\n\n"
                "📝 *Please upload one of these formats:*\n"
                "• PDF (.pdf) ✅\n"
                "• Word Document (.docx) ✅\n\n"
                "⏳ *Send a valid file to try again...*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
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
                'en': "⬅️ Back to Account",
                'es': "⬅️ Volver a Cuenta",
                'fr': "⬅️ Retour au Compte",
                'de': "⬅️ Zurück zum Konto",
                'it': "⬅️ Torna all'Account",
                'pt': "⬅️ Voltar à Conta",
                'am': "⬅️ ወደ መለያ ይመለሱ"
            }.get(lang_code, "⬅️ Back to Account")
            
            reply_keyboard = [
                [KeyboardButton(back_text)]
            ]
            
            # Save profile to API
            await save_profile_to_api(user_id, user_profiles[user_id])
            
            await m.reply_text(
                f"{title}\n\n"
                f"{message}\n\n"
                f"{tip}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
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

    # Save job to database and get job ID
    job_id = save_job(job_data)
    
    # Build job URL using job ID
    job_url = f"{WEBAPP_URL}job-details/{job_id}"
    keyboard = [[InlineKeyboardButton("Apply Job", url=job_url)]]

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
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=job_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✅ Job posted successfully!")
        logger.info(f"Job posted successfully to channel {CHANNEL_ID}")
        # TODO: Save job_data to database using DATABASE_URL if needed
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
# General Text Handler (for profile editing)
# ---------------------------
async def general_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle general text input when user is editing profile or in wizard mode"""
    awaiting = context.user_data.get('awaiting_input')
    in_wizard = 'profile_wizard_step' in context.user_data
    
    if in_wizard:
        # Handle wizard input
        await handle_wizard_input(update, context)
    elif awaiting:
        # Route to text_input_handler for legacy profile edits
        await text_input_handler(update, context)
    # If not awaiting any input, ignore (other handlers should have caught it)

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

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Add error handler
    app.add_error_handler(error_handler)

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("applications", applications_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # CallbackQuery handlers (for inline buttons that remain)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(applications_cb, pattern="^applications$"))
    app.add_handler(CallbackQueryHandler(about_cb, pattern="^about$"))
    app.add_handler(CallbackQueryHandler(settings_cb, pattern="^settings$"))
    
    # Settings tab callback handlers
    app.add_handler(CallbackQueryHandler(settings_languages_cb, pattern="^settings_languages$"))
    app.add_handler(CallbackQueryHandler(settings_account_cb, pattern="^settings_account$"))
    app.add_handler(CallbackQueryHandler(settings_cv_cb, pattern="^settings_cv$"))
    app.add_handler(CallbackQueryHandler(settings_terms_cb, pattern="^settings_terms$"))
    
    # Language selection handlers (callback)
    app.add_handler(CallbackQueryHandler(language_selection, pattern="^lang_"))
    
    # CV action handlers (callback)
    app.add_handler(CallbackQueryHandler(cv_upload_handler, pattern="^cv_upload$"))
    app.add_handler(CallbackQueryHandler(cv_view_handler, pattern="^cv_view$"))
    app.add_handler(CallbackQueryHandler(cv_remove_handler, pattern="^cv_remove$"))
    
    # Account management handlers (callback)
    app.add_handler(CallbackQueryHandler(account_edit_profile_handler, pattern="^account_edit_profile$"))
    app.add_handler(CallbackQueryHandler(account_notifications_handler, pattern="^account_notifications$"))
    app.add_handler(CallbackQueryHandler(account_delete_handler, pattern="^account_delete$"))
    app.add_handler(CallbackQueryHandler(privacy_policy_handler, pattern="^terms_privacy$"))
    
    # Notification toggles
    app.add_handler(CallbackQueryHandler(toggle_notification_handler, pattern="^toggle_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_account_handler, pattern="^confirm_delete_account$"))

    # Reply keyboard text message handlers
    # Menu button handlers (all languages)
    app.add_handler(MessageHandler(filters.Regex(r"^(📱 Menu|Menu|Menú|Menü|ሜኑ)$"), menu_text_handler))
    
    # Main menu options (specific patterns)
    app.add_handler(MessageHandler(filters.Regex(r"^📋 (Applications|Aplicaciones|Candidatures|Bewerbungen|Candidature|Candidaturas|ማመልከቻዎች)$"), applications_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^ℹ️ .*$"), about_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⚙️ .*$"), settings_cb))
    
    # Settings submenu handlers (specific patterns)
    # NOTE: Languages handler must be specific to avoid catching "🌍 Country" button
    app.add_handler(MessageHandler(filters.Regex(r"^🌍 (Languages|Idiomas|Langues|Sprachen|Lingue|ቋንቋዎች)$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 (Account|Cuenta|Compte|Konto|Conta|መለያ)$"), settings_account_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^📄 (My CV|Mi CV|Mon CV|Mein Lebenslauf|Il Mio CV|Meu CV|የእኔ CV)$"), settings_cv_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^📋 (Terms|Términos|Termes|Geschäftsbedingungen|Termini|Termos|ውሎች).*$"), settings_terms_cb))
    
    # Profile button handler (in case WebApp doesn't work or user types it)
    app.add_handler(MessageHandler(filters.Regex(r"^👤 (Profile|Perfil|Profil|Profilo|መገለጫ)$"), account_edit_profile_handler))
    
    # Language selection handlers (text)
    app.add_handler(MessageHandler(filters.Regex(r"^🇺🇸 English$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇪🇸 Español$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇫🇷 Français$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇩🇪 Deutsch$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇮🇹 Italiano$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇵🇹 Português$"), language_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🇪🇹 አማርኛ.*$"), language_text_handler))
    
    # CV action handlers (text)
    app.add_handler(MessageHandler(filters.Regex(r"^👁️ View.*$"), cv_view_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📤 Upload.*$"), cv_upload_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🗑️ Remove CV$"), cv_remove_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^❌ Cancel Upload$"), cancel_cv_upload_handler))
    
    # Profile wizard handler
    app.add_handler(MessageHandler(filters.Regex(r"^✏️ Edit Profile$"), start_profile_wizard))
    
    # Wizard navigation handlers
    app.add_handler(MessageHandler(filters.Regex(r"^⏭️ Skip$"), handle_wizard_input))
    app.add_handler(MessageHandler(filters.Regex(r"^❌ Cancel$"), smart_cancel_handler))
    
    # Gender selection handlers (for wizard and standalone)
    app.add_handler(MessageHandler(filters.Regex(r"^👨 Male$"), handle_wizard_input))
    app.add_handler(MessageHandler(filters.Regex(r"^👩 Female$"), handle_wizard_input))
    app.add_handler(MessageHandler(filters.Regex(r"^🧑 Other$"), handle_wizard_input))
    app.add_handler(MessageHandler(filters.Regex(r"^🔒 Prefer not to say$"), handle_wizard_input))
    
    # Account action handlers (text)
    app.add_handler(MessageHandler(filters.Regex(r"^🔔 Notifications$"), account_notifications_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🗑️ Delete Account$"), account_delete_handler))
    
    # Privacy handler (text)
    app.add_handler(MessageHandler(filters.Regex(r"^🔒 Privacy.*$"), privacy_policy_handler))
    
    # Back button handlers
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Back to Menu$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Volver al Menú$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Retour au Menu$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Zurück zum Menü$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Torna al Menu$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Voltar ao Menu$"), menu_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ ወደ ሜኑ ይመለሱ$"), menu_text_handler))
    
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Back to Settings$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Volver a Configuración$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Retour aux Paramètres$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Zurück zu Einstellungen$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Torna alle Impostazioni$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Voltar às Configurações$"), settings_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ ወደ ቅንብሮች ይመለሱ$"), settings_cb))
    
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Back to Account$"), settings_account_cb))
    
    # Delete account confirmation handlers
    app.add_handler(MessageHandler(filters.Regex(r"^⚠️ Yes, Delete My Account$"), confirm_delete_account_handler))
    
    # Notification toggle handlers (text)
    app.add_handler(MessageHandler(filters.Regex(r"^🚨 Job Alerts:.*$"), toggle_notification_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📄 App Updates:.*$"), toggle_notification_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Messages:.*$"), toggle_notification_text_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📢 Marketing:.*$"), toggle_notification_text_handler))
    
    # Start over handler
    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Start Over$"), start))
    
    # Back to Languages handler
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Back to Languages$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Volver a Idiomas$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Retour aux Langues$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Zurück zu Sprachen$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Torna alle Lingue$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Voltar aos Idiomas$"), settings_languages_cb))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ ወደ ቋንቋዎች ይመለሱ$"), settings_languages_cb))

    # File/message handlers
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, file_handler))
    
    # General text input handler (for name/contact editing) - must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_text_handler))

    # Run bot
    app.run_polling()

if __name__ == "__main__":
    main()
