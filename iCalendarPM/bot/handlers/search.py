from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from bot.database import async_session, UserCalendar
from bot.services.google_sheets_service import search_contacts_all_sheets
from bot.services.logger import send_log
import logging
from sqlalchemy import select

logger = logging.getLogger(__name__)

SEARCH_QUERY, SEARCH_RESULT = range(2)

async def search_contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == user_id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text(
                "❌ Сначала добавьте календарь: /addcalendar\n\n"
                "Для поиска почты нужен доступ к адресной книге.",
                disable_web_page_preview=True
            )
            return ConversationHandler.END
    
    await update.message.reply_text(
        "🔍 <b>Поиск почты по имени/фамилии</b>\n\n"
        "Введите фамилию и/или имя для поиска.\n"
        "Например: <code>Иванов</code> или <code>Иван Петров</code>\n\n"
        "Поиск ведётся по всем справочникам адресной книги.\n\n"
        "Отправьте /cancel для отмены.",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return SEARCH_QUERY

async def handle_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    logger.info(f"🔍 handle_search_text: пользователь {user_id}, текст: '{text}'")
    
    if text.startswith('/') or len(text) < 2:
        return
    
    if update.effective_chat.type != 'private':
        return
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == user_id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text(
                "❌ Сначала добавьте календарь: /addcalendar",
                disable_web_page_preview=True
            )
            return
    
    try:
        contacts = search_contacts_all_sheets(
            cal.caldav_username,
            cal.caldav_password_encrypted,
            text,
            limit=20
        )
        
        logger.info(f"   📊 Найдено контактов: {len(contacts)}")
        
        if not contacts:
            await update.message.reply_text(
                f"❌ По запросу '{text}' ничего не найдено.",
                disable_web_page_preview=True
            )
            await send_log(f"🔍 Поиск почты: {text} - ничего не найдено")
            return
        
        if len(contacts) == 1:
            c = contacts[0]
            msg = f"✅ <b>Найден контакт:</b>\n\n"
            msg += f"📌 <b>Имя:</b> {c.get('display_name', 'Не указано')}\n"
            msg += f"📧 <b>Email:</b> <code>{c.get('email')}</code>\n"
            if c.get('groups'):
                msg += f"📋 <b>Команда:</b> {c.get('groups')}\n"
            msg += f"📂 <b>Справочник:</b> {c.get('sheet', 'Неизвестно')}\n"
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            await send_log(f"🔍 Найдена почта: {c.get('email')} для {text}")
            return
        
        msg = f"🔍 <b>Найдено {len(contacts)} контактов:</b>\n\n"
        for i, c in enumerate(contacts[:15], 1):
            display_name = c.get('display_name', 'Без имени')
            email = c.get('email', '')
            groups = c.get('groups', '')
            sheet = c.get('sheet', 'Неизвестно')
            
            msg += f"{i}. <b>{display_name}</b>\n"
            msg += f"   📧 <code>{email}</code>\n"
            if groups:
                msg += f"   📋 Команда: {groups}\n"
            msg += f"   📂 Справочник: {sheet}\n\n"
        
        await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
        await send_log(f"🔍 Найдено {len(contacts)} контактов по запросу {text}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        await update.message.reply_text(
            f"❌ Ошибка поиска: {e}",
            disable_web_page_preview=True
        )
        await send_log(f"❌ Ошибка поиска: {e}")

async def search_contact_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if query_text.lower() in ['отмена', 'cancel']:
        await update.message.reply_text("❌ Поиск отменён.")
        return ConversationHandler.END
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == user_id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text(
                "❌ Календарь не найден. Сначала добавьте календарь: /addcalendar",
                disable_web_page_preview=True
            )
            return ConversationHandler.END
        
        try:
            contacts = search_contacts_all_sheets(
                cal.caldav_username,
                cal.caldav_password_encrypted,
                query_text,
                limit=20
            )
            
            if not contacts:
                await update.message.reply_text(
                    f"❌ По запросу '{query_text}' ничего не найдено.",
                    disable_web_page_preview=True
                )
                await send_log(f"🔍 Поиск почты: {query_text} - ничего не найдено")
                return ConversationHandler.END
            
            if len(contacts) == 1:
                c = contacts[0]
                msg = f"✅ <b>Найден контакт:</b>\n\n"
                msg += f"📌 <b>Имя:</b> {c.get('display_name', 'Не указано')}\n"
                msg += f"📧 <b>Email:</b> <code>{c.get('email')}</code>\n"
                if c.get('groups'):
                    msg += f"📋 <b>Команда:</b> {c.get('groups')}\n"
                msg += f"📂 <b>Справочник:</b> {c.get('sheet', 'Неизвестно')}\n"
                await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
                await send_log(f"🔍 Найдена почта: {c.get('email')} для {query_text}")
                return ConversationHandler.END
            
            msg = f"🔍 <b>Найдено {len(contacts)} контактов:</b>\n\n"
            for i, c in enumerate(contacts[:15], 1):
                display_name = c.get('display_name', 'Без имени')
                email = c.get('email', '')
                groups = c.get('groups', '')
                sheet = c.get('sheet', 'Неизвестно')
                
                msg += f"{i}. <b>{display_name}</b>\n"
                msg += f"   📧 <code>{email}</code>\n"
                if groups:
                    msg += f"   📋 Команда: {groups}\n"
                msg += f"   📂 Справочник: {sheet}\n\n"
            
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            await send_log(f"🔍 Найдено {len(contacts)} контактов по запросу {query_text}")
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text(
                f"❌ Ошибка поиска: {e}",
                disable_web_page_preview=True
            )
            await send_log(f"❌ Ошибка поиска: {e}")
            return ConversationHandler.END

async def search_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "search_cancel":
        await query.edit_message_text("❌ Поиск отменён.", disable_web_page_preview=True)
        return ConversationHandler.END
    
    if query.data.startswith("show_contact_"):
        email = query.data.replace("show_contact_", "")
        
        user_id = update.effective_user.id
        async with async_session() as session:
            result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == user_id))
            cal = result.scalars().first()
            if not cal:
                await query.edit_message_text("❌ Календарь не найден.", disable_web_page_preview=True)
                return ConversationHandler.END
            
            contacts = search_contacts_all_sheets(cal.caldav_username, cal.caldav_password_encrypted, email, limit=1)
            
            if contacts:
                c = contacts[0]
                msg = f"✅ <b>Контакт:</b>\n\n"
                msg += f"📌 <b>Имя:</b> {c.get('display_name', 'Не указано')}\n"
                msg += f"📧 <b>Email:</b> <code>{c.get('email')}</code>\n"
                if c.get('groups'):
                    msg += f"📋 <b>Команда:</b> {c.get('groups')}\n"
                msg += f"📂 <b>Справочник:</b> {c.get('sheet', 'Неизвестно')}\n"
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Скопировать почту", callback_data=f"copy_{email}")]
                ])
                await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
                await send_log(f"🔍 Показан контакт: {c.get('email')}")
            else:
                await query.edit_message_text(f"❌ Контакт с почтой {email} не найден.", disable_web_page_preview=True)
    
    elif query.data.startswith("copy_"):
        email = query.data.replace("copy_", "")
        await query.answer(text=f"📋 Почта скопирована: {email}", show_alert=True)
    
    return ConversationHandler.END

async def search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Поиск отменён.", disable_web_page_preview=True)
    return ConversationHandler.END
