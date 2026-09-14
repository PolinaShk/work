from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select
from bot.database import async_session, User, UserCalendar
from bot.services.caldav_service import CalDAVService, send_email_invite
from bot.services.google_sheets_service import GoogleSheetsService
from bot.services.logger import send_log
import logging
import re

logger = logging.getLogger(__name__)

EVT_SUMMARY, EVT_START, EVT_DURATION, EVT_LOCATION, EVT_ATTENDEES = range(5)
FORCE_CREATE = 99
FIND_FREE_TIME = 100
SELECT_CONTACT = 101

PROJECT_EMAIL = "mailbot@id-east.ru"
INCLUDE_WEEKENDS = False

async def safe_edit_message(message_obj, text, parse_mode=None, reply_markup=None, disable_web_page_preview=None):
    try:
        if hasattr(message_obj, 'edit_text'):
            await message_obj.edit_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview
            )
        elif hasattr(message_obj, 'edit_message_text'):
            await message_obj.edit_message_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview
            )
        return True
    except Exception as e:
        if "Message is not modified" not in str(e):
            raise e
        return False

async def check_access(user_id):
    async with async_session() as session:
        user = await session.get(User, user_id)
        return user and user.is_approved and not user.is_blocked

def add_required_attendees(attendees, author_email):
    required = set()
    required.add(PROJECT_EMAIL)
    if author_email:
        required.add(author_email)
    for attendee in attendees:
        if attendee and attendee.strip():
            required.add(attendee.strip())
    return list(required)

def validate_start_time(start_dt):
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    min_allowed = now + timedelta(minutes=15)
    if start_dt < min_allowed:
        return False, min_allowed
    return True, None

def send_regular_email_invites(summary, start, end, location, attendees, organizer_email):
    result = {"emailed": [], "failed": []}
    if not attendees:
        return result
    start_msk = start.astimezone(pytz.timezone("Europe/Moscow")) if start.tzinfo else pytz.timezone("Europe/Moscow").localize(start)
    end_msk = end.astimezone(pytz.timezone("Europe/Moscow")) if end.tzinfo else pytz.timezone("Europe/Moscow").localize(end)
    subject = f"Приглашение на встречу: {summary}"
    location_text = location if location else "не указано"
    body_text = f"""
Приглашение на встречу: {summary}

Дата и время: {start_msk.strftime('%d.%m.%Y %H:%M')} - {end_msk.strftime('%H:%M')}
Место: {location_text}

Организатор: {organizer_email}

---
Это приглашение создано автоматически через Telegram бота iCalendarPM.
    """
    body_html = f"""
<html>
<body>
<h2>Приглашение на встречу: {summary}</h2>
<p><b>Дата и время:</b> {start_msk.strftime('%d.%m.%Y %H:%M')} - {end_msk.strftime('%H:%M')}</p>
<p><b>Место:</b> {location_text}</p>
<p><b>Организатор:</b> {organizer_email}</p>
<hr>
<p><small>Это приглашение создано автоматически через Telegram бота iCalendarPM.</small></p>
</body>
</html>
    """
    for attendee in attendees:
        if attendee:
            if send_email_invite(attendee, subject, body_text, body_html, None):
                result["emailed"].append(attendee)
            else:
                result["failed"].append(attendee)
    return result

def is_email(text: str) -> bool:
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, text.strip()))

async def search_contact_by_name(query: str) -> list:
    try:
        service = GoogleSheetsService()
        contacts = service.search_contacts(query, limit=20, search_all_sheets=False)
        logger.info(f"Поиск по имени '{query}': найдено {len(contacts)} контактов")
        for c in contacts:
            logger.info(f"  - {c.get('display_name')} ({c.get('email')})")
        return contacts
    except Exception as e:
        logger.error(f"Ошибка поиска контакта: {e}")
        return []

async def create_event_start(update, context):
    if not await check_access(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["event_attendees"] = []
    context.user_data["creating"] = False
    await update.message.reply_text(
        "📝 <b>Создание новой встречи</b>\n\n"
        "Введите название встречи:\n\n"
        "Отправьте /cancel для отмены",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return EVT_SUMMARY

async def event_summary(update, context):
    context.user_data["event_summary"] = update.message.text
    await update.message.reply_text(
        "📅 Введите дату и время начала (ДД.ММ.ГГГГ ЧЧ:ММ):\n"
        "Например: 13.05.2026 14:30\n\n"
        "Отправьте /cancel для отмены",
        disable_web_page_preview=True
    )
    return EVT_START

async def event_start_time(update, context):
    try:
        dt = datetime.strptime(update.message.text, "%d.%m.%Y %H:%M")
        tz = pytz.timezone("Europe/Moscow")
        start_dt = tz.localize(dt)
        is_valid, min_time = validate_start_time(start_dt)
        if not is_valid:
            await update.message.reply_text(
                f"❌ Нельзя создавать встречи в прошлом или менее чем через 15 минут.\n\n"
                f"🕐 Минимальное время: {min_time.strftime('%d.%m.%Y %H:%M')}\n\n"
                "Отправьте /cancel для отмены",
                disable_web_page_preview=True
            )
            return EVT_START
        context.user_data["event_start_time"] = start_dt
        await update.message.reply_text(
            "⏱ Введите длительность в минутах (15-480):\n"
            "Например: 60\n\n"
            "Отправьте /cancel для отмены",
            disable_web_page_preview=True
        )
        return EVT_DURATION
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Отправьте /cancel для отмены",
            disable_web_page_preview=True
        )
        return EVT_START

async def event_duration(update, context):
    try:
        d = int(update.message.text)
        if d < 15 or d > 480:
            raise ValueError
        context.user_data["event_duration"] = d
        await update.message.reply_text(
            "📍 Введите место встречи (или '-' чтобы пропустить):\n\n"
            "Отправьте /cancel для отмены",
            disable_web_page_preview=True
        )
        return EVT_LOCATION
    except ValueError:
        await update.message.reply_text(
            "❌ Введите число от 15 до 480 (минут):\n\n"
            "Отправьте /cancel для отмены",
            disable_web_page_preview=True
        )
        return EVT_DURATION

async def event_location(update, context):
    location = update.message.text
    context.user_data["event_location"] = location if location != "-" else ""
    await update.message.reply_text(
        "👥 Введите email участника или его имя/фамилию для поиска.\n\n"
        "• Если введёте email — он сразу добавится\n"
        "• Если введёте имя/фамилию — бот покажет список найденных\n"
        "Можно ввести несколько через запятую\n"
        "Или отправьте '-' чтобы пропустить\n\n"
        "Пример: user@company.ru или Иванов\n\n"
        "Отправьте /cancel для отмены",
        disable_web_page_preview=True
    )
    return EVT_ATTENDEES

async def event_attendees(update, context):
    text = update.message.text
    if text == "-":
        context.user_data["event_attendees"] = context.user_data.get("event_attendees", [])
        return await check_conflicts(update, context)
    
    parts = [p.strip() for p in text.split(",") if p.strip()]
    attendees = context.user_data.get("event_attendees", [])
    found_contacts = []
    not_found = []
    contacts_to_select = []
    
    for part in parts:
        if is_email(part):
            if part not in attendees:
                attendees.append(part)
                found_contacts.append(f"📧 {part}")
        else:
            contacts = await search_contact_by_name(part)
            if len(contacts) == 1:
                email = contacts[0].get('email')
                if email and email not in attendees:
                    attendees.append(email)
                    display_name = contacts[0].get('display_name') or f"{contacts[0].get('first_name', '')} {contacts[0].get('last_name', '')}".strip()
                    groups = contacts[0].get('groups', '')
                    if groups:
                        found_contacts.append(f"🔍 {display_name} → {email} ({groups})")
                    else:
                        found_contacts.append(f"🔍 {display_name} → {email}")
            elif len(contacts) > 1:
                contacts_to_select.extend(contacts)
            else:
                not_found.append(part)
    
    context.user_data["event_attendees"] = attendees
    
    if contacts_to_select:
        keyboard_buttons = []
        for contact in contacts_to_select[:10]:
            display_name = contact.get('display_name') or f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
            email = contact.get('email', '')
            groups = contact.get('groups', '')
            if email:
                button_text = f"{display_name} ({email})"
                if groups:
                    button_text += f" [{groups}]"
                callback_data = f"add_contact_{email}"
                keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        keyboard_buttons.append([InlineKeyboardButton("❌ Пропустить всех", callback_data="skip_all")])
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        await update.message.reply_text(
            "🔍 Найдено несколько контактов. Выберите кого добавить:\n\n"
            "Или нажмите 'Пропустить всех', чтобы продолжить без них.\n\n"
            "Отправьте /cancel для отмены",
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        context.user_data["pending_contacts"] = contacts_to_select
        return SELECT_CONTACT
    
    msg = ""
    if found_contacts:
        msg += "✅ Добавлены участники:\n" + "\n".join(found_contacts) + "\n\n"
    if not_found:
        msg += f"❌ Не найдены: {', '.join(not_found)}\n\n"
    
    if attendees:
        current_list = "\n".join([f"• {e}" for e in attendees])
        msg += f"📋 Текущие участники:\n{current_list}\n\n"
    
    msg += "Можете добавить ещё или отправьте '-' чтобы завершить\n"
    msg += "Отправьте /cancel для отмены"
    
    await update.message.reply_text(msg, disable_web_page_preview=True)
    return EVT_ATTENDEES

async def select_contact_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if not query.data:
        return
    
    if query.data == "skip_all":
        await query.edit_message_text("⏭️ Вы пропустили выбор. Продолжайте добавление участников.", disable_web_page_preview=True)
        return EVT_ATTENDEES
    
    if query.data.startswith("add_contact_"):
        email = query.data.replace("add_contact_", "")
        attendees = context.user_data.get("event_attendees", [])
        
        pending = context.user_data.get("pending_contacts", [])
        contact_name = email
        for c in pending:
            if c.get('email') == email:
                contact_name = c.get('display_name') or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
                break
        
        if email not in attendees:
            attendees.append(email)
            context.user_data["event_attendees"] = attendees
        
        await query.edit_message_text(
            f"✅ Добавлен: {contact_name} ({email})\n\n"
            "Теперь вы можете добавить ещё участников или завершить.\n"
            "Отправьте '-' чтобы завершить.\n"
            "Отправьте /cancel для отмены",
            disable_web_page_preview=True
        )
        
        current_list = "\n".join([f"• {e}" for e in attendees])
        if current_list:
            await update.message.reply_text(f"📋 Текущие участники:\n{current_list}", disable_web_page_preview=True)
        
        context.user_data["pending_contacts"] = []
        return EVT_ATTENDEES
    
    return EVT_ATTENDEES

async def check_conflicts(update, context):
    start = context.user_data.get("event_start_time")
    duration = context.user_data.get("event_duration")
    end = start + timedelta(minutes=duration)
    summary = context.user_data.get("event_summary")
    location = context.user_data.get("event_location", "")
    attendees = context.user_data.get("event_attendees", [])
    
    if not attendees:
        async with async_session() as session:
            result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
            cal = result.scalars().first()
            if not cal:
                await update.message.reply_text("❌ Календарь не найден.\nОтправьте /cancel для отмены")
                return ConversationHandler.END
            return await create_event_final(update, context, cal)
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text("❌ Календарь не найден.\nОтправьте /cancel для отмены")
            return ConversationHandler.END
        try:
            service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
            existing_events = service.get_events(start, end)
            conflicts = [e for e in existing_events if e["start"] < end and e["end"] > start]
            if conflicts:
                msg = "⚠️ <b>Обнаружены конфликты:</b>\n\n"
                for e in sorted(conflicts, key=lambda x: x["start"])[:5]:
                    st = e["start"].astimezone(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
                    et = e["end"].astimezone(pytz.timezone("Europe/Moscow")).strftime("%H:%M")
                    msg += f"📌 <b>{e['summary']}</b>\n🕐 {st} - {et}\n\n"
                msg += "Что делаем?"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да, создать", callback_data="force_create")],
                    [InlineKeyboardButton("🔍 Найти свободное время", callback_data="find_free_time")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
                ])
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
                return FORCE_CREATE
            return await create_event_final(update, context, cal)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}\nОтправьте /cancel для отмены")
            return ConversationHandler.END

async def create_event_final(update, context, cal):
    summary = context.user_data.get("event_summary")
    start = context.user_data.get("event_start_time")
    duration = context.user_data.get("event_duration")
    location = context.user_data.get("event_location", "")
    attendees = context.user_data.get("event_attendees", [])
    end = start + timedelta(minutes=duration)
    context.user_data["creating"] = False
    try:
        service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
        final_attendees = add_required_attendees(attendees, cal.caldav_username)
        description = f"Встреча создана через Telegram бота iCalendarPM\n"
        if final_attendees:
            description += f"\nУчастники: {', '.join(final_attendees)}\n"
        success, uid = service.create_event(summary, start, end, location, final_attendees, description)
        if success:
            display_attendees = [att for att in final_attendees if att != PROJECT_EMAIL]
            msg = f"✅ <b>Встреча создана!</b>\n\n"
            msg += f"📌 <b>{summary}</b>\n"
            msg += f"🕐 {start.strftime('%d.%m.%Y %H:%M')} - {end.strftime('%H:%M')}\n"
            if location:
                msg += f"📍 {location}\n"
            if display_attendees:
                msg += f"\n👥 <b>Участники:</b>\n"
                for att in display_attendees:
                    msg += f"• {att}\n"
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            await send_log(f"✅ Встреча создана: {summary} (участники: {len(final_attendees)})")
            try:
                email_result = send_regular_email_invites(summary, start, end, location, final_attendees, cal.caldav_username)
                await send_log(f"📧 Приглашения отправлены на email: {len(email_result.get('emailed', []))} успешно")
            except Exception as e:
                await send_log(f"⚠️ Ошибка отправки email-приглашений: {e}")
        else:
            await update.message.reply_text("❌ Ошибка при создании встречи")
            await send_log(f"❌ Ошибка создания встречи: {summary}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}\nОтправьте /cancel для отмены")
        await send_log(f"❌ Ошибка создания встречи {summary}: {e}")
    return ConversationHandler.END

async def force_create_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_create":
        await safe_edit_message(query, "❌ Отменено.")
        return ConversationHandler.END
    if context.user_data.get("creating", False):
        await query.answer("⏳ Встреча уже создаётся, подождите...", show_alert=True)
        return ConversationHandler.END
    context.user_data["creating"] = True
    await safe_edit_message(query, "⏳ Создаю встречу...", disable_web_page_preview=True)
    if query.data == "find_free_time":
        return await find_free_time_handler(update, context)
    start = context.user_data.get("event_start_time")
    duration = context.user_data.get("event_duration")
    end = start + timedelta(minutes=duration)
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await safe_edit_message(query, "❌ Календарь не найден.")
            context.user_data["creating"] = False
            return ConversationHandler.END
        result_msg = await create_event_final(update, context, cal)
        context.user_data["creating"] = False
        return result_msg

async def find_free_time_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_find_free_time":
        await safe_edit_message(query, "❌ Поиск отменён.")
        context.user_data["creating"] = False
        return ConversationHandler.END
    summary = context.user_data.get("event_summary")
    duration = context.user_data.get("event_duration")
    location = context.user_data.get("event_location", "")
    user_attendees = context.user_data.get("event_attendees", [])
    original_start = context.user_data.get("original_start_time")
    if not all([summary, duration, original_start]):
        await safe_edit_message(query, "❌ Данные утеряны.")
        context.user_data["creating"] = False
        return ConversationHandler.END
    await safe_edit_message(query, "🔍 Ищу свободное время...", disable_web_page_preview=True)
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await safe_edit_message(query, "❌ Календарь не найден.")
            context.user_data["creating"] = False
            return ConversationHandler.END
        try:
            from bot.handlers.create_event import find_all_free_slots
            service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
            free_slots = find_all_free_slots(service, original_start, duration, max_days=14, max_slots=10)
            if not free_slots:
                await safe_edit_message(query, "❌ Не найдено свободное время.")
                context.user_data["creating"] = False
                return ConversationHandler.END
            context.user_data["free_slots"] = {slot.timestamp(): slot for slot in free_slots}
            keyboard_buttons = []
            weekdays_ru = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
            for slot in free_slots[:10]:
                slot_end = slot + timedelta(minutes=duration)
                weekday = weekdays_ru[slot.weekday()]
                button_text = f"{weekday} {slot.strftime('%d.%m.%Y %H:%M')} - {slot_end.strftime('%H:%M')}"
                keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=f"free_slot_{slot.timestamp()}")])
            keyboard_buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_find_free_time")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            msg = f"🔍 <b>Найдено {len(free_slots)} свободных окон:</b>\n\n📌 {summary} ({duration} мин)\n\nВыберите время:"
            await safe_edit_message(query, msg, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
            context.user_data["creating"] = False
            return FIND_FREE_TIME
        except Exception as e:
            await safe_edit_message(query, f"❌ Ошибка: {str(e)[:200]}")
            context.user_data["creating"] = False
            return ConversationHandler.END

async def find_free_time_confirm_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_find_free_time":
        await safe_edit_message(query, "❌ Отменено.")
        context.user_data["creating"] = False
        return ConversationHandler.END
    if query.data.startswith("free_slot_"):
        try:
            timestamp = float(query.data.split("_")[2])
        except (IndexError, ValueError):
            await safe_edit_message(query, "❌ Ошибка формата.")
            context.user_data["creating"] = False
            return ConversationHandler.END
        start = context.user_data.get("free_slots", {}).get(timestamp)
        if not start:
            await safe_edit_message(query, "❌ Время больше недоступно.")
            context.user_data["creating"] = False
            return ConversationHandler.END
        context.user_data["event_start_time"] = start
        await safe_edit_message(query, "⏳ Создаю встречу...", disable_web_page_preview=True)
        async with async_session() as session:
            result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
            cal = result.scalars().first()
            if not cal:
                await safe_edit_message(query, "❌ Календарь не найден.")
                context.user_data["creating"] = False
                return ConversationHandler.END
            result_msg = await create_event_final(update, context, cal)
            context.user_data["creating"] = False
            return result_msg
    context.user_data["creating"] = False
    return ConversationHandler.END

def find_all_free_slots(service, start_date, duration, max_days=14, max_slots=10):
    work_start = 9
    work_end = 18
    step_minutes = 15
    all_free_slots = []
    selected_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_offset = 0
    days_checked = 0
    while len(all_free_slots) < max_slots and days_checked <= max_days:
        current_day = selected_day + timedelta(days=day_offset)
        if not INCLUDE_WEEKENDS and current_day.weekday() >= 5:
            day_offset += 1
            continue
        current = current_day.replace(hour=work_start, minute=0, second=0, microsecond=0)
        day_end = current_day.replace(hour=work_end, minute=0, second=0, microsecond=0)
        if day_offset == 0 and start_date.hour >= work_start:
            current = start_date
            minutes = current.minute
            remainder = minutes % step_minutes
            if remainder != 0:
                current = current.replace(minute=minutes + (step_minutes - remainder), second=0, microsecond=0)
        while current < day_end and len(all_free_slots) < max_slots:
            slot_end = current + timedelta(minutes=duration)
            if slot_end > day_end:
                break
            try:
                events = service.get_events(current, slot_end)
                conflicts = [e for e in events if e["start"] < slot_end and e["end"] > current]
                if not conflicts:
                    all_free_slots.append(current)
                    current += timedelta(minutes=step_minutes)
                else:
                    conflict_end = max([e["end"] for e in conflicts])
                    current = conflict_end
                    minutes = current.minute
                    remainder = minutes % step_minutes
                    if remainder != 0:
                        current = current.replace(minute=minutes + (step_minutes - remainder), second=0, microsecond=0)
            except Exception as e:
                logger.error(f"Ошибка проверки слота: {e}")
                current += timedelta(minutes=step_minutes)
        day_offset += 1
        days_checked += 1
    return all_free_slots

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Создание встречи отменено.")
    return ConversationHandler.END
