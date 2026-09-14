from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select
from bot.database import async_session, User, UserCalendar
from bot.services.caldav_service import CalDAVService, send_email_invite
from bot.services.zoom_service import ZoomService
from bot.services.google_sheets_service import GoogleSheetsService
from bot.services.logger import send_log
import logging
import re

logger = logging.getLogger(__name__)

SUMMARY, START_TIME, DURATION, LOCATION, ATTENDEES = range(5)
FORCE_CREATE_ZOOM = 99
FIND_FREE_TIME_ZOOM = 100

PROJECT_EMAIL = "mailbot@id-east.ru"

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

def is_email(text: str) -> bool:
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, text.strip()))

async def search_contact_by_name(query: str) -> list:
    try:
        service = GoogleSheetsService()
        contacts = service.search_contacts(query, limit=5, search_all_sheets=False)
        logger.info(f"Zoom поиск по имени '{query}': найдено {len(contacts)} контактов")
        return contacts
    except Exception as e:
        logger.error(f"Ошибка поиска контакта: {e}")
        return []

def validate_start_time(start_dt):
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    min_allowed = now + timedelta(minutes=15)
    if start_dt < min_allowed:
        return False, min_allowed
    return True, None

async def find_free_slot_fast(service, start_date, duration, max_days=7):
    work_start_hour = 9
    work_end_hour = 18
    current = start_date
    end_search = start_date + timedelta(days=max_days)
    while current < end_search:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            current = current.replace(hour=work_start_hour, minute=0, second=0)
            continue
        hour = current.hour
        if hour < work_start_hour:
            current = current.replace(hour=work_start_hour, minute=0)
        elif hour >= work_end_hour:
            current += timedelta(days=1)
            current = current.replace(hour=work_start_hour, minute=0)
            continue
        slot_end = current + timedelta(minutes=duration)
        try:
            events = service.get_events(current, slot_end)
            conflicts = [e for e in events if e["start"] < slot_end and e["end"] > current]
            if not conflicts:
                return current
        except Exception as e:
            print(f"Check slot error: {e}")
        current += timedelta(hours=1)
    return None

async def create_meeting_start(update, context):
    if not await check_access(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "🎥 <b>Создание Zoom-встречи</b>\n\n"
        "Введите название встречи:\n\n"
        "(для отмены отправьте /cancel)",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return SUMMARY

async def meeting_summary(update, context):
    context.user_data["zoom_summary"] = update.message.text
    await update.message.reply_text(
        "🕐 Дата и время начала (ДД.ММ.ГГГГ ЧЧ:ММ):\n"
        "Например: 13.05.2026 14:30",
        disable_web_page_preview=True
    )
    return START_TIME

async def meeting_start_time(update, context):
    try:
        dt = datetime.strptime(update.message.text, "%d.%m.%Y %H:%M")
        tz = pytz.timezone("Europe/Moscow")
        start_dt = tz.localize(dt)
        is_valid, min_time = validate_start_time(start_dt)
        if not is_valid:
            await update.message.reply_text(
                f"❌ Нельзя создавать встречи в прошлом или менее чем через 15 минут.\n\n"
                f"🕐 Минимальное время: {min_time.strftime('%d.%m.%Y %H:%M')}",
                disable_web_page_preview=True
            )
            return START_TIME
        context.user_data["zoom_start_time"] = start_dt
        await update.message.reply_text(
            "⏱ Длительность в минутах (15-480):\n"
            "Например: 60",
            disable_web_page_preview=True
        )
        return DURATION
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ",
            disable_web_page_preview=True
        )
        return START_TIME

async def meeting_duration(update, context):
    try:
        d = int(update.message.text)
        if d < 15 or d > 480:
            raise ValueError
        context.user_data["zoom_duration"] = d
        await update.message.reply_text(
            "📍 Место проведения (или - чтобы пропустить):",
            disable_web_page_preview=True
        )
        return LOCATION
    except ValueError:
        await update.message.reply_text(
            "❌ Введите число от 15 до 480 (минут):",
            disable_web_page_preview=True
        )
        return DURATION

async def meeting_location(update, context):
    location = update.message.text
    context.user_data["zoom_location"] = location if location != "-" else ""
    await update.message.reply_text(
        "👥 Введите email участника или его имя/фамилию для поиска.\n\n"
        "• Если введёте email — он сразу добавится\n"
        "• Если введёте имя/фамилию — бот найдёт email в адресной книге\n\n"
        "Можно ввести несколько через запятую\n"
        "Или отправьте '-' чтобы пропустить\n\n"
        "Пример: user@company.ru или Иванов",
        disable_web_page_preview=True
    )
    return ATTENDEES

async def meeting_attendees(update, context):
    text = update.message.text
    
    if text == "-":
        context.user_data["zoom_attendees"] = context.user_data.get("zoom_attendees", [])
        return await check_zoom_conflicts(update, context)
    
    parts = [p.strip() for p in text.split(",") if p.strip()]
    
    attendees = context.user_data.get("zoom_attendees", [])
    found_contacts = []
    not_found = []
    
    for part in parts:
        if is_email(part):
            if part not in attendees:
                attendees.append(part)
                found_contacts.append(f"📧 {part}")
        else:
            contacts = await search_contact_by_name(part)
            if contacts:
                for contact in contacts:
                    email = contact.get('email')
                    if email and email not in attendees:
                        attendees.append(email)
                        display_name = contact.get('display_name') or f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
                        groups = contact.get('groups', '')
                        if groups:
                            found_contacts.append(f"🔍 {display_name} → {email} ({groups})")
                        else:
                            found_contacts.append(f"🔍 {display_name} → {email}")
            else:
                not_found.append(part)
    
    context.user_data["zoom_attendees"] = attendees
    
    msg = ""
    if found_contacts:
        msg += "✅ Добавлены участники:\n" + "\n".join(found_contacts) + "\n\n"
    if not_found:
        msg += f"❌ Не найдены: {', '.join(not_found)}\n\n"
    
    if attendees:
        current_list = "\n".join([f"• {e}" for e in attendees])
        msg += f"📋 Текущие участники:\n{current_list}\n\n"
    
    msg += "Можете добавить ещё или отправьте '-' чтобы завершить"
    
    await update.message.reply_text(msg, disable_web_page_preview=True)
    return ATTENDEES

async def check_zoom_conflicts(update, context):
    start = context.user_data.get("zoom_start_time")
    duration = context.user_data.get("zoom_duration")
    end = start + timedelta(minutes=duration)
    summary = context.user_data.get("zoom_summary")
    location = context.user_data.get("zoom_location", "")
    attendees = context.user_data.get("zoom_attendees", [])
    
    if not attendees:
        async with async_session() as session:
            result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
            cal = result.scalars().first()
            if not cal:
                await update.message.reply_text("❌ Календарь не найден.")
                return ConversationHandler.END
            return await create_zoom_final(update, context, cal)
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text("❌ Сначала добавьте календарь: /addcalendar")
            return ConversationHandler.END
        
        try:
            conflicts = []
            
            caldav_service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
            existing_events = caldav_service.get_events(start, end)
            cal_conflicts = [e for e in existing_events if e["start"] < end and e["end"] > start]
            conflicts.extend(cal_conflicts)
            
            zoom = ZoomService()
            try:
                start_utc = start.astimezone(pytz.UTC)
                end_utc = end.astimezone(pytz.UTC)
                zoom_conflicts = await zoom.check_conflicts(start_utc, end_utc)
                for zc in zoom_conflicts:
                    conflicts.append({
                        "summary": zc["topic"],
                        "start": datetime.strptime(zc["start_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC),
                        "end": datetime.strptime(zc["start_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC) + timedelta(minutes=60),
                        "location": "Zoom",
                        "attendees": [zc.get("host_email", "")]
                    })
            except Exception as e:
                print(f"Zoom error: {e}")
            await zoom.close()
            
            if conflicts:
                msg = "⚠️ <b>Обнаружены конфликты:</b>\n\n"
                for e in sorted(conflicts, key=lambda x: x["start"])[:5]:
                    st = e["start"].astimezone(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
                    et = e["end"].astimezone(pytz.timezone("Europe/Moscow")).strftime("%H:%M")
                    msg += f"📌 <b>{e['summary']}</b>\n🕐 {st} - {et}\n\n"
                msg += "Что делаем?"
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да, создать", callback_data="force_create_zoom")],
                    [InlineKeyboardButton("🔍 Найти свободное время", callback_data="find_free_time_zoom")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create_zoom")]
                ])
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
                return FORCE_CREATE_ZOOM
            
            return await create_zoom_final(update, context, cal)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
            return ConversationHandler.END

async def create_zoom_final(update, context, cal):
    summary = context.user_data.get("zoom_summary")
    start = context.user_data.get("zoom_start_time")
    duration = context.user_data.get("zoom_duration")
    location = context.user_data.get("zoom_location", "")
    attendees = context.user_data.get("zoom_attendees", [])
    end = start + timedelta(minutes=duration)
    
    try:
        attendees = add_required_attendees(attendees, cal.caldav_username)
        
        zoom = ZoomService()
        meeting = await zoom.create_meeting(summary, start, duration, attendees)
        
        zoom_location = f"Zoom Meeting\n🔗 {meeting['join_url']}"
        if meeting.get("password"):
            zoom_location += f"\n🔑 Код: {meeting['password']}"
        if location:
            zoom_location += f"\n📍 {location}"
        
        description = f"Zoom ссылка: {meeting['join_url']}\n"
        if meeting.get("password"):
            description += f"Код доступа: {meeting['password']}\n"
        if location:
            description += f"Место: {location}\n"
        if attendees:
            description += f"\nУчастники: {', '.join(attendees)}\n"
        description += f"\nСоздано через бота iCalendarPM"
        
        caldav_service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
        success, uid = caldav_service.create_event(summary, start, end, zoom_location, attendees, description)
        
        if success:
            send_zoom_email_invites(summary, start, end, location, attendees, cal.caldav_username, meeting['join_url'], meeting.get("password"))
            display_attendees = [att for att in attendees if att != PROJECT_EMAIL]
            
            msg = f"✅ <b>Zoom-встреча создана!</b>\n\n"
            msg += f"📌 <b>{summary}</b>\n"
            msg += f"🔗 {meeting['join_url']}\n"
            msg += f"🕐 {start.strftime('%d.%m.%Y %H:%M')} - {end.strftime('%H:%M')}\n"
            if meeting.get("password"):
                msg += f"🔑 Код: <code>{meeting['password']}</code>\n"
            if location:
                msg += f"📍 {location}\n"
            if display_attendees:
                msg += f"\n👥 <b>Участники:</b>\n"
                for att in display_attendees:
                    msg += f"• {att}\n"
            msg += f"\n📧 Приглашения отправлены на email участников."
            
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            await send_log(f"✅ Пользователь {update.effective_user.id} создал Zoom встречу: {summary}")
            await zoom.close()
            return True
        else:
            await zoom.close()
            return False
            
    except Exception as e:
        error_msg = str(e)
        if "1114" in error_msg:
            error_msg = "Некоторые участники не зарегистрированы в Zoom."
        elif "401" in error_msg:
            error_msg = "Ошибка авторизации Zoom."
        await update.message.reply_text(f"❌ Ошибка: {error_msg}", disable_web_page_preview=True)
        await send_log(f"❌ Ошибка создания Zoom встречи: {e}")
        return False

def send_zoom_email_invites(summary, start, end, location, attendees, organizer_email, zoom_link, zoom_password):
    if not attendees:
        return
    start_msk = start.astimezone(pytz.timezone("Europe/Moscow")) if start.tzinfo else pytz.timezone("Europe/Moscow").localize(start)
    end_msk = end.astimezone(pytz.timezone("Europe/Moscow")) if end.tzinfo else pytz.timezone("Europe/Moscow").localize(end)
    subject = f"Приглашение на Zoom встречу: {summary}"
    location_text = f"Zoom Meeting\nСсылка: {zoom_link}"
    if zoom_password:
        location_text += f"\nКод: {zoom_password}"
    if location:
        location_text += f"\n📍 {location}"
    body_text = f"""
Приглашение на Zoom встречу: {summary}
Дата и время: {start_msk.strftime('%d.%m.%Y %H:%M')} - {end_msk.strftime('%H:%M')}
Место: {location_text}
Организатор: {organizer_email}
---
Это приглашение создано автоматически через Telegram бота iCalendarPM.
    """
    body_html = f"""
<html>
<body>
<h2>Приглашение на Zoom встречу: {summary}</h2>
<p><b>Дата и время:</b> {start_msk.strftime('%d.%m.%Y %H:%M')} - {end_msk.strftime('%H:%M')}</p>
<p><b>Место:</b><br>
🔗 <a href='{zoom_link}'>{zoom_link}</a><br>
{"🔑 Код: " + zoom_password + "<br>" if zoom_password else ""}
{location if location else ''}
</p>
<p><b>Организатор:</b> {organizer_email}</p>
<hr>
<p><small>Это приглашение создано автоматически через Telegram бота iCalendarPM.</small></p>
</body>
</html>
    """
    for attendee in attendees:
        if attendee:
            send_email_invite(attendee, subject, body_text, body_html, None)

async def force_create_zoom_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_create_zoom":
        await safe_edit_message(query, "❌ Отменено.")
        return ConversationHandler.END
    if query.data == "find_free_time_zoom":
        await safe_edit_message(query, "🔍 Ищу свободное время...", disable_web_page_preview=True)
        return await find_free_time_zoom_handler(update, context)
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await safe_edit_message(query, "❌ Календарь не найден.")
            return ConversationHandler.END
        return await create_zoom_final(update, context, cal)

async def find_free_time_zoom_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_find_free_time_zoom":
        await safe_edit_message(query, "❌ Поиск отменён.")
        return ConversationHandler.END
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await safe_edit_message(query, "❌ Календарь не найден.")
            return ConversationHandler.END
        
        summary = context.user_data.get("zoom_summary")
        start = context.user_data.get("zoom_start_time")
        duration = context.user_data.get("zoom_duration")
        
        try:
            service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
            free_start = await find_free_slot_fast(service, start, duration)
            if not free_start:
                await safe_edit_message(query, "❌ Не найдено свободное время.")
                return ConversationHandler.END
            
            context.user_data["zoom_start_time"] = free_start
            free_end = free_start + timedelta(minutes=duration)
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Создать", callback_data="find_free_time_zoom_confirm")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_find_free_time_zoom")]
            ])
            msg = f"🔍 <b>Найдено свободное время:</b>\n\n"
            msg += f"📌 <b>{summary}</b>\n"
            msg += f"🕐 {free_start.strftime('%d.%m.%Y %H:%M')} - {free_end.strftime('%H:%M')}\n"
            await safe_edit_message(query, msg, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
            return FIND_FREE_TIME_ZOOM
        except Exception as e:
            await safe_edit_message(query, f"❌ Ошибка: {str(e)[:200]}")
            return ConversationHandler.END

async def find_free_time_zoom_confirm_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_find_free_time_zoom":
        await safe_edit_message(query, "❌ Отменено.")
        return ConversationHandler.END
    if query.data == "find_free_time_zoom_confirm":
        async with async_session() as session:
            result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
            cal = result.scalars().first()
            if not cal:
                await safe_edit_message(query, "❌ Календарь не найден.")
                return ConversationHandler.END
            return await create_zoom_final(update, context, cal)
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Создание Zoom встречи отменено.", disable_web_page_preview=True)
    return ConversationHandler.END
