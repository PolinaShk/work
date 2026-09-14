from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select
from bot.database import async_session, User, UserCalendar
from bot.services.caldav_service import CalDAVService
from bot.services.logger import send_log

async def check_access(user_id):
    async with async_session() as session:
        user = await session.get(User, user_id)
        return user and user.is_approved and not user.is_blocked

# ========== НАСТРОЙКИ УВЕДОМЛЕНИЙ ==========

async def notification_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update.effective_user.id):
        return
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if not cal:
            await update.message.reply_text("❌ Сначала добавьте календарь: /addcalendar")
            return
        d = "✅" if cal.notification_daily else "❌"
        w = "✅" if cal.notification_weekly else "❌"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Ежедневно: {d}", callback_data="toggle_daily")],
            [InlineKeyboardButton(f"Еженедельно: {w}", callback_data="toggle_weekly")],
        ])
        
        await update.message.reply_text(
            "🔔 <b>Настройки уведомлений:</b>\n\n"
            "• Ежедневно (Пн-Пт в 8:45) — встречи на сегодня\n"
            "• Еженедельно (Пн в 8:45) — встречи на неделю\n\n"
            "Нажмите на кнопку, чтобы включить/выключить:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

async def toggle_daily(update, context):
    query = update.callback_query
    await query.answer()
    
    if not await check_access(update.effective_user.id):
        return
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if cal:
            cal.notification_daily = not cal.notification_daily
            await session.commit()
            s = "✅ включены" if cal.notification_daily else "❌ выключены"
            
            d = "✅" if cal.notification_daily else "❌"
            w = "✅" if cal.notification_weekly else "❌"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Ежедневно: {d}", callback_data="toggle_daily")],
                [InlineKeyboardButton(f"Еженедельно: {w}", callback_data="toggle_weekly")],
            ])
            await query.edit_message_text(
                f"🔔 <b>Настройки уведомлений:</b>\n\n"
                f"Ежедневно: {s}\n"
                f"Еженедельно: {'✅ включены' if cal.notification_weekly else '❌ выключены'}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

async def toggle_weekly(update, context):
    query = update.callback_query
    await query.answer()
    
    if not await check_access(update.effective_user.id):
        return
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).where(UserCalendar.user_id == update.effective_user.id))
        cal = result.scalars().first()
        if cal:
            cal.notification_weekly = not cal.notification_weekly
            await session.commit()
            s = "✅ включены" if cal.notification_weekly else "❌ выключены"
            
            d = "✅" if cal.notification_daily else "❌"
            w = "✅" if cal.notification_weekly else "❌"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Ежедневно: {d}", callback_data="toggle_daily")],
                [InlineKeyboardButton(f"Еженедельно: {w}", callback_data="toggle_weekly")],
            ])
            await query.edit_message_text(
                f"🔔 <b>Настройки уведомлений:</b>\n\n"
                f"Ежедневно: {'✅ включены' if cal.notification_daily else '❌ выключены'}\n"
                f"Еженедельно: {s}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )


# ========== ОТПРАВКА УВЕДОМЛЕНИЙ ==========

async def send_notifications(application):
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    
    # Проверяем только 45-ю минуту
    if now.minute != 45:
        return
    
    async with async_session() as session:
        result = await session.execute(select(UserCalendar).join(User).where(
            User.is_approved == True, 
            User.is_blocked == False
        ))
        calendars = result.scalars().all()
        
        sent_count = 0
        error_count = 0
        users_with_events = []
        users_without_events = []
        
        for cal in calendars:
            user_tz = pytz.timezone(cal.timezone)
            local_now = now.astimezone(user_tz)
            
            # Проверяем 8-й час
            if local_now.hour != 8:
                continue
            
            is_monday = (local_now.weekday() == 0)
            is_weekday = (local_now.weekday() < 5)
            
            # Если нет ни одной галочки — пропускаем
            if not (cal.notification_daily or cal.notification_weekly):
                continue
            
            # ============ ЕЖЕНЕДЕЛЬНЫЕ (только по понедельникам) ============
            if cal.notification_weekly and is_monday:
                start_week = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_week = start_week + timedelta(days=7)
                
                try:
                    service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
                    week_events = service.get_events(start_week, end_week)
                    future_events = [e for e in week_events if e["start"].astimezone(user_tz) >= local_now]
                    
                    if future_events:
                        msg = f"🔔 <b>Ваши встречи на неделю ({start_week.strftime('%d.%m')} - {(end_week - timedelta(days=1)).strftime('%d.%m')}):</b>\n\n"
                        current_date = None
                        for e in sorted(future_events, key=lambda x: x["start"]):
                            start_local = e["start"].astimezone(user_tz)
                            end_local = e["end"].astimezone(user_tz)
                            date_str = start_local.strftime("%d.%m (%a)")
                            
                            if date_str != current_date:
                                current_date = date_str
                                msg += f"📅 <b>{date_str}</b>\n"
                            
                            recurring = " 🔁" if e.get("is_recurring") else ""
                            msg += f"   🕐 {start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}\n"
                            msg += f"   📌 {e['summary']}{recurring}\n"
                            if e.get("location"):
                                msg += f"   📍 {e['location']}\n"
                            msg += "\n"
                        
                        await application.bot.send_message(chat_id=cal.user_id, text=msg, parse_mode="HTML")
                        sent_count += 1
                        users_with_events.append(cal.user_id)
                        await send_log(f"📅 Еженедельное уведомление отправлено пользователю {cal.user_id}: {len(future_events)} событий")
                    else:
                        users_without_events.append(cal.user_id)
                        await send_log(f"📭 Еженедельное уведомление пользователю {cal.user_id}: событий нет")
                except Exception as e:
                    error_count += 1
                    await send_log(f"❌ Ошибка еженедельного уведомления для {cal.user_id}: {e}")
                
                # В понедельник НЕ отправляем ежедневные, даже если они включены
                continue
            
            # ============ ЕЖЕДНЕВНЫЕ (ПН-ПТ, кроме понедельника) ============
            if cal.notification_daily and is_weekday and not is_monday:
                start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                try:
                    service = CalDAVService(cal.caldav_url, cal.caldav_username, cal.caldav_password_encrypted)
                    events = service.get_events(start, end)
                    future_events = [e for e in events if e["start"].astimezone(user_tz) >= local_now]
                    
                    if future_events:
                        msg = f"🔔 <b>Ваши встречи сегодня ({local_now.strftime('%d.%m.%Y')}):</b>\n\n"
                        for e in sorted(future_events, key=lambda x: x["start"]):
                            start_local = e["start"].astimezone(user_tz)
                            end_local = e["end"].astimezone(user_tz)
                            recurring = " 🔁" if e.get("is_recurring") else ""
                            msg += f"🕐 {start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}\n"
                            msg += f"📌 {e['summary']}{recurring}\n"
                            if e.get("location"):
                                msg += f"📍 {e['location']}\n"
                            msg += "\n"
                        
                        await application.bot.send_message(chat_id=cal.user_id, text=msg, parse_mode="HTML")
                        sent_count += 1
                        users_with_events.append(cal.user_id)
                        await send_log(f"📨 Ежедневное уведомление отправлено пользователю {cal.user_id}: {len(future_events)} событий")
                    else:
                        users_without_events.append(cal.user_id)
                        await send_log(f"📭 Ежедневное уведомление пользователю {cal.user_id}: событий нет")
                except Exception as e:
                    error_count += 1
                    await send_log(f"❌ Ошибка ежедневного уведомления для {cal.user_id}: {e}")
        
        # ========== ГЛАВНЫЙ ЛОГ О ВЫПОЛНЕНИИ ЗАДАЧИ ==========
        log_msg = f"📊 <b>Ежедневное уведомление выполнено</b>\n"
        log_msg += f"📅 <b>{now.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        log_msg += f"📊 Всего пользователей с календарём: <b>{len(calendars)}</b>\n"
        
        if sent_count > 0:
            log_msg += f"📨 Получили уведомления: <b>{sent_count}</b> пользователей\n"
            log_msg += f"👤 ID: <code>{', '.join(map(str, users_with_events))}</code>\n"
        else:
            log_msg += f"📭 Уведомления не отправлены (нет событий или уведомления отключены)\n"
        
        if users_without_events:
            log_msg += f"⏭️ Без событий: <b>{len(users_without_events)}</b> пользователей\n"
        
        if error_count > 0:
            log_msg += f"❌ Ошибок: <b>{error_count}</b>\n"
        
        await send_log(log_msg)

async def _send_events_message(application, user_id, events, user_tz, period_text):
    if not events:
        return
    
    msg = f"🔔 <b>Ваши встречи {period_text}:</b>\n\n"
    current_date = None
    
    for e in sorted(events, key=lambda x: x["start"]):
        start_local = e["start"].astimezone(user_tz)
        end_local = e["end"].astimezone(user_tz)
        date_str = start_local.strftime("%d.%m (%a)")
        
        if period_text == "на неделю" and date_str != current_date:
            current_date = date_str
            msg += f"📅 <b>{date_str}</b>\n"
        
        recurring = " 🔁" if e.get("is_recurring") else ""
        
        if period_text == "сегодня":
            msg += f"🕐 {start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}\n"
            msg += f"📌 {e['summary']}{recurring}\n"
        else:
            msg += f"   🕐 {start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}\n"
            msg += f"   📌 {e['summary']}{recurring}\n"
        
        if e.get("location"):
            msg += f"   📍 {e['location']}\n"
        if e.get("attendees"):
            attendees = [a for a in e['attendees'] if a != "mailbot@id-east.ru"]
            if attendees:
                msg += f"   👥 {', '.join(attendees[:2])}\n"
        msg += "\n"
    
    try:
        await application.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML")
    except Exception as e:
        print(f"Failed to send message to {user_id}: {e}")
