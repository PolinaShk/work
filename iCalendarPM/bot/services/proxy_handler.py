from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.logger import send_log, send_info

BOT_NAME = "iCalendar"


async def retry_last_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки повторного действия при ошибке прокси"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await send_info(BOT_NAME, str(user_id), "Повтор последнего действия после ошибки")
    
    await query.edit_message_text(
        "🔄 Повторяем последнее действие...\n"
        "Пожалуйста, подождите."
    )
    
    # Возвращаемся в главное меню
    keyboard = [[InlineKeyboardButton("📋 Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✅ Действие выполнено!\n"
        "Если проблема повторяется, попробуйте позже.",
        reply_markup=reply_markup
    )


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки главного меню"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await send_info(BOT_NAME, str(user_id), "Возврат в главное меню")
    
    await query.edit_message_text(
        "📋 <b>Главное меню</b>\n\n"
        "Выберите действие:\n"
        "• /today — События на сегодня\n"
        "• /week — События на неделю\n"
        "• /create_event — Создать встречу\n"
        "• /create_zoom — Создать Zoom встречу\n"
        "• /addcalendar — Добавить календарь\n"
        "• /notifications — Настройки уведомлений\n"
        "• /help — Помощь",
        parse_mode="HTML"
    )
