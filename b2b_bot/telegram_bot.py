import logging
import socket
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest
import httpx

import config
import sheets_client
import processing
import logger

# === ПРИНУДИТЕЛЬНОЕ ИСПОЛЬЗОВАНИЕ ТОЛЬКО IPv4 ===
original_getaddrinfo = socket.getaddrinfo
def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = ipv4_only_getaddrinfo

logger_telegram = logging.getLogger(__name__)

CONFIRM_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("Подтверждаю", callback_data="delete_expired")]])

async def log_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if not message:
        return
    user_name = user.username or f"{user.first_name} {user.last_name or ''}".strip() or str(user.id)
    chat_name = chat.title or chat.username or "личка"
    if message.text:
        await logger.log_action(f"📩 Сообщение от @{user_name} в {chat_name}", "telegram", {"Текст": message.text[:500], "Chat ID": chat.id, "User ID": user.id})
    elif message.document:
        await logger.log_action(f"📁 Файл от @{user_name} в {chat_name}", "telegram", {"Имя файла": message.document.file_name, "Chat ID": chat.id, "User ID": user.id})
    else:
        await logger.log_action(f"📩 Сообщение (не текст) от @{user_name} в {chat_name}", "telegram", {"Тип": message.effective_attachment.__class__.__name__ if message.effective_attachment else "неизвестно", "Chat ID": chat.id, "User ID": user.id})

def build_application():
    logger_telegram.info(f"🔐 Используем API: {config.TELEGRAM_API_URL}")
    logger_telegram.info(f"🔐 Токен: {config.TELEGRAM_BOT_TOKEN[:10]}...")
    
    timeout = httpx.Timeout(60.0, connect=30.0, read=60.0, write=30.0)
    http_client = httpx.Client(timeout=timeout)
    
    try:
        request = HTTPXRequest(client=http_client)
    except TypeError:
        request = None
    
    builder = Application.builder().token(config.TELEGRAM_BOT_TOKEN).base_url(config.TELEGRAM_API_URL)
    if request:
        builder = builder.request(request)
    app = builder.build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run_now", cmd_run_now))
    app.add_handler(CommandHandler("delete", cmd_delete_expired))
    app.add_handler(CallbackQueryHandler(on_confirm_delete, pattern="^delete_expired$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_user_message))
    return app

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = ("🤖 <b>Бот управления заявками B2B-Center</b>\n\n"
        "📊 <b>Что делает:</b>\n"
        "• 📬 Раз в день проверяет почту на новые приглашения к процедурам\n"
        "• 🌐 Сам заходит на B2B-Center и забирает закупочные позиции\n"
        "• 📝 Записывает их в таблицы\n"
        "• 🔔 Уведомляет партнёров о новых заявках\n"
        "• ⏰ Напоминает об истекающих сроках подачи\n\n"
        "📌 <b>Команды:</b>\n"
        "• /start — это сообщение\n"
        "• /status — статус бота\n"
        "• /run_now — запустить проверку почты и парсинг прямо сейчас\n"
        "• /delete — удалить просроченные заявки (только для админа)\n\n"
        f"🆔 Chat ID этого чата: <code>{chat.id}</code>")
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🤖 <b>Статус бота</b>\n\n"
        f"✅ Бот работает\n"
        f"🔐 API: {config.TELEGRAM_API_URL}\n"
        f"⏰ Проверка почты: 9:00 и 17:00\n"
        f"⏰ Проверка сроков: каждый час с 9:30 до 17:30 (пн–пт)\n"
        f"🗑️ Удаление просроченных: автоматическое\n"
        f"📊 Google Sheets: подключен",
        parse_mode="HTML"
    )

async def cmd_run_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username or update.effective_user.id
    logger_telegram.info("Пользователь %s запустил /run_now", user)
    await update.message.reply_text("🔄 <b>Запускаю принудительную проверку...</b>\n\n⏳ Это может занять 1-3 минуты. Отчёт пришлю сюда же.", parse_mode="HTML")
    try:
        async def notify_admin(text):
            await update.message.reply_text(text, parse_mode="HTML")
        async def notify_partners_wrapper(new_requests):
            await notify_partners(context.application, new_requests)
        await processing.process_new_procedures(notify_admin, notify_partners_wrapper)
        async def notify_admin_with_keyboard(text):
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=CONFIRM_KEYBOARD)
        await processing.check_expiring(notify_admin_with_keyboard, notify_admin)
        await update.message.reply_text("✅ <b>Принудительная проверка завершена!</b>", parse_mode="HTML")
    except Exception as e:
        logger_telegram.exception("Ошибка в /run_now")
        await update.message.reply_text(f"❌ <b>Ошибка:</b>\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

async def cmd_delete_expired(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username or update.effective_user.id
    chat_id = str(update.effective_chat.id)
    if chat_id != str(config.TELEGRAM_ADMIN_CHAT_ID):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    logger_telegram.info("Админ %s запустил /delete", user)
    await update.message.reply_text("🔄 Удаляю просроченные заявки...")
    async def notify_result(text):
        await update.message.reply_text(text, parse_mode="HTML")
    await processing.delete_expired(notify_result)

async def on_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user.username or query.from_user.id
    logger_telegram.info("Пользователь %s нажал 'Подтверждаю'", user)
    async def notify_result(text):
        await query.message.reply_text(text, parse_mode="HTML")
    await processing.delete_expired(notify_result)
    await query.message.delete()

async def send_admin_message(app, text):
    await app.bot.send_message(chat_id=config.TELEGRAM_ADMIN_CHAT_ID, text=text, parse_mode="HTML")

async def send_admin_message_with_confirm(app, text):
    await app.bot.send_message(chat_id=config.TELEGRAM_ADMIN_CHAT_ID, text=text, parse_mode="HTML", reply_markup=CONFIRM_KEYBOARD)

async def notify_partners(app, new_requests):
    service = sheets_client.get_service(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    partners = sheets_client.get_partners(service, config.SHEET_PARTNERS_ID)
    if not partners:
        await send_admin_message(app, "⚠️ Нет партнёров для рассылки")
        return
    
    message = "📢 <b>НОВЫЕ ЗАПРОСЫ НА ИТ-СПЕЦИАЛИСТОВ!</b>\n\n"
    message += f"📊 <b>Всего новых запросов:</b> {len(new_requests)}\n\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    for i, req in enumerate(new_requests, 1):
        name = req["name"][:150] + ("..." if len(req["name"]) > 150 else "")
        message += f"\n<b>{i}. Запрос №{req['number']}</b>\n"
        message += f"📅 <b>Подача до:</b> {req['date']}\n"
        message += f"📝 {name}\n"
    message += "\n━━━━━━━━━━━━━━━━━━━━\n"
    message += f'🔗 <a href="https://docs.google.com/spreadsheets/d/{config.SHEET_TABLE1_ID}/edit">👉 Открыть таблицу</a>\n\n'
    
    # ДОБАВЛЯЕМ ВАЖНОЕ СООБЩЕНИЕ ПАРТНЁРАМ
    message += "📌 <b>Важно!</b> Обновился шаблон резюме.\n"
    message += "📄 Актуальный шаблон можно скачать по ссылке:\n"
    message += "🔗 https://drive.id-east.ru/s/5926ksYh0gQS2mW\n"
    message += "⚠️ Пожалуйста, используйте только этот шаблон для подачи заявок!"
    
    success, failed = 0, 0
    for partner in partners:
        try:
            await app.bot.send_message(chat_id=partner["chat_id"], text=message, parse_mode="HTML", disable_web_page_preview=True)
            success += 1
        except Exception as e:
            failed += 1
            logger_telegram.warning("Не удалось отправить партнёру %s: %s", partner["name"], e)
    await send_admin_message(app, f"📨 Рассылка: успешно {success}, ошибок {failed}")
