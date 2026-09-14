from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes
from bot.services.google_sheets_service import GoogleSheetsService
import logging

logger = logging.getLogger(__name__)

async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик инлайн-поиска контактов.
    Работает как автодополнение при вводе имени/фамилии/email.
    """
    query = update.inline_query.query.strip()
    
    # Если запрос пустой или слишком короткий — не отвечаем
    if len(query) < 2:
        await update.inline_query.answer([], cache_time=1)
        return
    
    try:
        # Ищем контакты в Google Sheets
        service = GoogleSheetsService()
        contacts = service.search_contacts(query, limit=10, search_all_sheets=True)
        
        if not contacts:
            results = [InlineQueryResultArticle(
                id="not_found",
                title=f"❌ Ничего не найдено для '{query}'",
                description="Попробуйте другой запрос",
                input_message_content=InputTextMessageContent(
                    f"❌ По запросу '{query}' ничего не найдено"
                )
            )]
            await update.inline_query.answer(results, cache_time=1)
            return
        
        # Формируем результаты
        results = []
        for i, contact in enumerate(contacts):
            email = contact.get('email', '')
            display_name = contact.get('display_name', '')
            
            if not email:
                continue
            
            title = f"{display_name} ({email})"
            description = f"📧 {email}"
            
            results.append(InlineQueryResultArticle(
                id=str(i),
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(email)
            ))
        
        await update.inline_query.answer(results, cache_time=1)
        
    except Exception as e:
        logger.error(f"Inline search error: {e}")
        try:
            await update.inline_query.answer([], cache_time=1)
        except:
            pass
