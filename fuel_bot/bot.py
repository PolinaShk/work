# -*- coding: utf-8 -*-
import asyncio
import logging
import logging.handlers
import json
import os
import sys
from datetime import datetime, timedelta
import datetime as dt
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import config
import db_helper
from api_helper import refresh_data, fetch_transaction_data, fetch_transaction_data_for_card

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, 'bot.log'),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

TOKEN = config.BOT_TOKEN
TG_API_URL = config.TG_API_URL
ADMIN_CHAT_ID = config.ADMIN_CHAT_ID
LOGS_CHAT_ID = config.LOGS_CHAT_ID
LOGS_TOPIC_ID = config.LOGS_TOPIC_ID
ADMINS = config.ADMINS
USE_PROXY = getattr(config, 'USE_PROXY', False)
PROXY_URL = getattr(config, 'PROXY_URL', None)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

STATE_FILE = "job_state.json"


def get_moscow_time():
    return datetime.now(MOSCOW_TZ)


def is_weekend():
    """Проверка: сегодня суббота или воскресенье (по МСК)"""
    return get_moscow_time().weekday() >= 5


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки {STATE_FILE}: {e}")
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {STATE_FILE}: {e}")


def mark_done(key):
    state = load_state()
    state[key] = get_moscow_time().strftime('%Y-%m-%d')
    save_state(state)


def is_done_today(key):
    state = load_state()
    return state.get(key) == get_moscow_time().strftime('%Y-%m-%d')


def format_number(num):
    try:
        return "{:,.2f}".format(float(num or 0)).replace(",", " ")
    except (ValueError, TypeError):
        return "0.00"


def mask_card_number(num):
    s = str(num)
    if len(s) <= 10:
        return s
    return s[:4] + '*' * (len(s) - 10) + s[-6:]


def load_users():
    if os.path.exists("users.json"):
        try:
            with open("users.json", 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки users.json: {e}")
    return {}


def save_users(users):
    try:
        with open("users.json", 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения users.json: {e}")


def is_admin(user_id):
    return str(user_id) in ADMINS


def get_user_name_by_card(card_number):
    users = load_users()
    clean_card = str(card_number).replace(' ', '').replace('-', '').strip()
    for user_id, data in users.items():
        cards = data.get('cards') or []
        if data.get('card_number'):
            cards = list(cards) + [data.get('card_number')]
        for c in cards:
            db_card = str(c).replace(' ', '').replace('-', '').strip()
            if db_card == clean_card:
                return data.get('name', 'неизвестно')
    return 'неизвестно'


def get_last_update_text():
    last_update = db_helper.get_last_update_time()
    if last_update == "никогда":
        return "⏳ Данные еще не обновлялись"
    try:
        dt_obj = datetime.fromisoformat(last_update)
        dt_moscow = dt_obj.astimezone(MOSCOW_TZ)
        return f"🔄 Данные обновлены: {dt_moscow.strftime('%d.%m.%Y в %H:%M')} (МСК)"
    except:
        return f"🔄 Данные обновлены: {last_update}"


def get_report_text():
    """Отчёт для администратора — со строкой 'последняя транзакция в данных'."""
    data = fetch_transaction_data()
    if not data or not data.get('time_stack'):
        balance = data.get('balance', 0) if data else 0
        return (f"<b>📊 Ежедневный отчет по топливу</b>\n\n"
                f"<b>💰 Доступный остаток: {format_number(balance)} руб.</b>\n\n"
                f"❌ Нет данных о транзакциях.\n{get_last_update_text()}")

    lines = []
    i = 1
    for t, card, cost in zip(data['time_stack'][:5], data['card_number_stack'][:5], data['base_cost_5_stack'][:5]):
        try:
            dt_obj = datetime.fromisoformat(t.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
        except:
            dt_obj = t
        user_name = get_user_name_by_card(card)
        lines.append(
            f"\n{i}. {dt_obj}\n"
            f"Номер карты: {mask_card_number(card)}\n"
            f"Сумма: {format_number(cost)} руб.\n"
            f"Комментарий: {user_name}\n"
        )
        i += 1

    # Дата самой свежей транзакции в данных — только для админа
    last_tx_date = "—"
    try:
        last_tx_date = datetime.fromisoformat(
            data['time_stack'][0].replace("Z", "+00:00")
        ).astimezone(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M')
    except Exception:
        pass

    return (f"<b>📊 Ежедневный отчет по топливу</b>\n\n"
            f"<b>💰 Доступный остаток: {format_number(data['balance'])} руб.</b>\n\n"
            f"🔸 <b>Последние транзакции</b>\n{''.join(lines)}\n"
            f"📌 <b>Последняя транзакция в данных:</b> {last_tx_date}\n\n"
            f"{get_last_update_text()}")


def get_personal_report_text(card_number, days=7):
    """Отчёт для пользователя — БЕЗ лишней информации."""
    data = fetch_transaction_data_for_card(card_number, days)
    if not data or not data.get('time_stack'):
        return (f"❌ Нет данных по карте {mask_card_number(card_number)} за последние {days} дней.\n\n"
                f"{get_last_update_text()}")

    transactions = sorted(zip(data['time_stack'], data['cost_stack']), key=lambda x: x[0], reverse=True)
    lines = []
    total_sum = 0
    i = 1
    for t, cost in transactions:
        try:
            dt_obj = datetime.fromisoformat(t.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except:
            dt_obj = t
        total_sum += cost
        lines.append(f"{i}. {dt_obj} — {format_number(cost)} руб.")
        i += 1

    user_name = get_user_name_by_card(card_number)
    return (f"<b>📊 Отчет по карте {mask_card_number(card_number)}</b>\n"
            f"👤 Владелец: {user_name}\n"
            f"📅 За последние {days} дней\n"
            f"💰 Всего: {format_number(total_sum)} руб.\n"
            f"📋 Количество операций: {len(lines)}\n\n"
            + "\n".join(lines) + f"\n\n{get_last_update_text()}")


def get_monthly_report_text(card_number=None):
    now = get_moscow_time()

    if now.day <= 5:
        if now.month == 1:
            year = now.year - 1
            month = 12
        else:
            year = now.year
            month = now.month - 1
    else:
        year = now.year
        month = now.month

    first_day = MOSCOW_TZ.localize(datetime(year, month, 1, 0, 0, 0))
    if month == 12:
        last_day = MOSCOW_TZ.localize(datetime(year + 1, 1, 1, 0, 0, 0)) - timedelta(seconds=1)
    else:
        last_day = MOSCOW_TZ.localize(datetime(year, month + 1, 1, 0, 0, 0)) - timedelta(seconds=1)

    month_name = f"{month:02d}.{year}"

    def parse_timestamp(ts):
        try:
            dt_utc = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt_utc.astimezone(MOSCOW_TZ)
        except Exception:
            return None

    is_final_report = is_last_working_day_of_month() and now.hour >= 6

    footer_note = ""
    if is_final_report:
        footer_note = (
            "\n\n📌 <i>Все операции, произведённые после 06:00 (МСК) сегодняшнего дня, "
            "будут включены в отчёт следующего месяца.</i>"
        )

    if card_number:
        data = fetch_transaction_data_for_card(card_number, 35)
        if not data or not data.get('time_stack'):
            return f"❌ Нет данных по карте {mask_card_number(card_number)} за {month_name}."

        filtered_times = []
        filtered_costs = []
        cutoff_time = None
        if is_final_report:
            cutoff_time = MOSCOW_TZ.localize(datetime(now.year, now.month, now.day, 6, 0, 0))

        for t, cost in zip(data['time_stack'], data['cost_stack']):
            dt_obj = parse_timestamp(t)
            if dt_obj:
                if first_day <= dt_obj <= last_day:
                    if cutoff_time and dt_obj > cutoff_time:
                        continue
                    filtered_times.append(t)
                    filtered_costs.append(cost)

        if not filtered_times:
            return f"❌ Нет данных по карте {mask_card_number(card_number)} за {month_name}."

        transactions = sorted(zip(filtered_times, filtered_costs), key=lambda x: x[0], reverse=True)
        lines = []
        total_sum = 0
        for t, cost in transactions:
            dt_obj = parse_timestamp(t)
            date_str = dt_obj.strftime("%Y-%m-%d") if dt_obj else t[:10]
            total_sum += cost
            lines.append(f"{date_str} — {format_number(cost)} руб.")

        user_name = get_user_name_by_card(card_number)
        return (f"<b>📊 МЕСЯЧНЫЙ ОТЧЕТ ЗА {month_name}</b>\n"
                f"💳 Карта: {mask_card_number(card_number)}\n"
                f"👤 Владелец: {user_name}\n"
                f"<b>💰 ИТОГО ЗА МЕСЯЦ: {format_number(total_sum)} руб.</b>\n"
                f"📋 Операций: {len(lines)}\n\n"
                + "\n".join(lines[-15:]) + footer_note + f"\n\n{get_last_update_text()}")

    else:
        data = fetch_transaction_data()
        if not data or not data.get('time_stack'):
            return f"❌ Нет данных за {month_name}."

        user_totals = {}
        cutoff_time = None
        if is_final_report:
            cutoff_time = MOSCOW_TZ.localize(datetime(now.year, now.month, now.day, 6, 0, 0))

        for t, card, cost in zip(data['time_stack'], data['card_number_stack'], data['base_cost_5_stack']):
            dt_obj = parse_timestamp(t)
            if dt_obj and first_day <= dt_obj <= last_day:
                if cutoff_time and dt_obj > cutoff_time:
                    continue
                user_name = get_user_name_by_card(card)
                if user_name not in user_totals:
                    user_totals[user_name] = 0
                user_totals[user_name] += cost

        if not user_totals:
            return f"❌ Нет данных за {month_name}."

        sorted_users = sorted(user_totals.items(), key=lambda x: x[1], reverse=True)
        total_sum = sum(user_totals.values())

        lines = []
        for user_name, amount in sorted_users:
            lines.append(f"<u>{user_name}</u> — <b>{format_number(amount)} руб.</b>")

        return (f"<b>📊 МЕСЯЧНЫЙ ОТЧЕТ ПО ВСЕМ КАРТАМ ЗА {month_name}</b>\n"
                f"<b>💰 ИТОГО: {format_number(total_sum)} руб.</b>\n"
                f"👥 Всего сотрудников: {len(lines)}\n\n"
                + "\n".join(lines) + footer_note + f"\n\n{get_last_update_text()}")


def is_last_working_day_of_month():
    today = get_moscow_time()
    if today.month == 12:
        last_day = datetime(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(today.year, today.month + 1, 1) - timedelta(days=1)

    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)

    return today.date() == last_day.date()


async def send_log_to_topic(text, bot=None):
    try:
        if bot is None:
            logger.warning("⚠️ Bot не передан для отправки лога")
            return False

        await bot.send_message(
            chat_id=LOGS_CHAT_ID,
            text=text,
            parse_mode='HTML',
            message_thread_id=LOGS_TOPIC_ID
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки лога: {e}")
        return False


async def send_with_retry(bot, chat_id, text, parse_mode='HTML', max_retries=5, delay=10, **kwargs):
    last_error = None
    for attempt in range(max_retries):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                **kwargs
            )
            return True
        except Exception as e:
            last_error = e
            logger.error(f"❌ Ошибка отправки (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)
                logger.info(f"⏳ Повтор через {wait_time} секунд...")
                await asyncio.sleep(wait_time)

    logger.error(f"❌ Не удалось отправить сообщение после {max_retries} попыток: {last_error}")
    return False


async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_name = user.full_name or user.username or str(user.id)
    user_id = user.id

    if update.message and update.message.text:
        is_command = update.message.text.startswith('/')
        msg_type = "Команда" if is_command else "Сообщение"
        text = update.message.text[:500] + ('...' if len(update.message.text) > 500 else '')

        log_text = (
            f"💬 <b>{msg_type}</b>\n"
            f"👤 Пользователь: {user_name}\n"
            f"🆔 ID: {user_id}\n"
            f"📝 Текст: {text}"
        )

        await send_log_to_topic(log_text, context.bot)
        logger.info(f"{msg_type} от {user_name} ({user_id}): {text[:100]}")

    elif update.message:
        msg = update.message
        if msg.photo:
            caption = msg.caption or "без подписи"
            log_text = (
                f"🖼️ <b>Фото</b>\n"
                f"👤 Пользователь: {user_name}\n"
                f"🆔 ID: {user_id}\n"
                f"📝 Подпись: {caption[:200]}"
            )
            await send_log_to_topic(log_text, context.bot)
        elif msg.video:
            log_text = (
                f"🎬 <b>Видео</b>\n"
                f"👤 Пользователь: {user_name}\n"
                f"🆔 ID: {user_id}"
            )
            await send_log_to_topic(log_text, context.bot)
        elif msg.document:
            log_text = (
                f"📄 <b>Документ</b>\n"
                f"👤 Пользователь: {user_name}\n"
                f"🆔 ID: {user_id}\n"
                f"📎 Имя: {msg.document.file_name}"
            )
            await send_log_to_topic(log_text, context.bot)
        elif msg.sticker:
            log_text = (
                f"🏷️ <b>Стикер</b>\n"
                f"👤 Пользователь: {user_name}\n"
                f"🆔 ID: {user_id}"
            )
            await send_log_to_topic(log_text, context.bot)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if is_admin(user_id):
        text = (
            "👑 <b>Добро пожаловать, администратор!</b>\n\n"
            "📋 <b>Доступные команды:</b>\n"
            "/adminhelp — список команд администратора\n"
            "/bind номер_карты ID_пользователя Имя — привязать карту\n"
            "/unbind ID_пользователя — отвязать карту\n"
            "/cards — все карты\n"
            "/users — все пользователи\n"
            "/report — общий отчет\n"
            "/monthly_report — принудительный месячный отчет\n"
            "/my — отчет за 7 дней\n"
            "/my30 — отчет за месяц\n"
            "/help — справка"
        )
    else:
        text = (
            "👋 <b>Привет! Я бот для отчетов по топливу</b>\n\n"
            "📋 <b>Команды:</b>\n"
            "/my — отчет за 7 дней\n"
            "/my30 — отчет за месяц\n"
            "/help — справка\n\n"
            "📅 <b>Автоотчеты (МСК):</b>\n"
            "• Понедельник в 9:20 — еженедельный\n"
            "• Последний рабочий день в 9:20 — месячный\n\n"
            "🔔 Обратитесь к администратору для привязки карты."
        )
    await update.message.reply_text(text, parse_mode='HTML')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if is_admin(user_id):
        text = (
            "👑 <b>Справка для администратора</b>\n\n"
            "/adminhelp — список команд администратора\n"
            "/bind номер_карты ID_пользователя Имя — привязать карту\n"
            "/unbind ID_пользователя — отвязать карту\n"
            "/cards — все карты\n"
            "/users — все пользователи\n"
            "/report — общий отчет\n"
            "/monthly_report — принудительный месячный отчет\n"
            "/my — отчет за 7 дней\n"
            "/my30 — отчет за месяц\n\n"
            "📅 <b>Автоотчеты (МСК):</b>\n"
            "• Будние дни в 9:15 — админам\n"
            "• Понедельник в 9:20 — пользователям\n"
            "• Последний рабочий день в 9:20 — месяц"
        )
    else:
        text = (
            "📋 <b>Помощь по боту</b>\n\n"
            "/my — отчет за 7 дней\n"
            "/my30 — отчет за месяц\n"
            "/help — справка\n\n"
            "📅 <b>Автоотчеты (МСК):</b>\n"
            "• Понедельник в 9:20 — еженедельный\n"
            "• Последний рабочий день в 9:20 — месячный\n\n"
            "🔔 Обратитесь к администратору для привязки карты."
        )
    await update.message.reply_text(text, parse_mode='HTML')


async def adminhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    text = (
        "👑 <b>Команды администратора:</b>\n\n"
        "/bind номер_карты ID_пользователя Имя — привязать карту\n"
        "/unbind ID_пользователя — отвязать карту\n"
        "/cards — показать все карты\n"
        "/users — показать всех пользователей\n"
        "/report — общий отчет\n"
        "/monthly_report — принудительный месячный отчет\n"
        "/adminhelp — эта справка\n\n"
        "📌 Как найти ID пользователя: попросите написать боту любое сообщение"
    )
    await update.message.reply_text(text, parse_mode='HTML')


async def bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ Используйте: /bind номер_карты ID_пользователя Имя")
        return
    card_number = args[0]
    user_id_target = args[1]
    name = " ".join(args[2:])

    if user_id_target in ADMINS:
        await update.message.reply_text("❌ Нельзя привязать карту к администратору!")
        return

    users = load_users()
    entry = users.get(user_id_target, {})
    cards = entry.get('cards') or []
    if entry.get('card_number') and entry.get('card_number') not in cards:
        cards = list(cards) + [entry.get('card_number')]
    if card_number not in cards:
        cards.append(card_number)
    users[user_id_target] = {"cards": cards, "name": name}
    save_users(users)

    await update.message.reply_text(f"✅ Карта {mask_card_number(card_number)} привязана к {name}")

    try:
        await context.bot.send_message(
            chat_id=int(user_id_target),
            text=f"👋 Вам привязали карту!\n\n💳 Карта: {mask_card_number(card_number)}\n👤 Имя: {name}"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение пользователю {user_id_target}: {e}")


async def unbind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Используйте: /unbind ID_пользователя")
        return
    user_id_target = args[0]
    users = load_users()
    if user_id_target not in users:
        await update.message.reply_text("❌ Пользователь не найден")
        return
    entry = users[user_id_target]
    name = entry.get("name", "Пользователь")
    cards = entry.get("cards") or ([entry["card_number"]] if entry.get("card_number") else [])
    del users[user_id_target]
    save_users(users)
    cards_str = ", ".join(mask_card_number(c) for c in cards) if cards else "—"
    await update.message.reply_text(f"✅ Карты {cards_str} отвязаны от {name}")
    try:
        await context.bot.send_message(chat_id=int(user_id_target), text="🔔 Ваши карты отвязаны!")
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение пользователю {user_id_target}: {e}")


async def cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    data = fetch_transaction_data()
    if data and data.get('number_stack'):
        cards_list = "\n".join(["• " + str(c) for c in data['number_stack']])
        users = load_users()
        bound = []
        for uid, info in users.items():
            cards = info.get('cards') or ([info.get('card_number')] if info.get('card_number') else [])
            for c in cards:
                if c:
                    bound.append(f"  • {mask_card_number(c)} — {uid} ({info.get('name', 'без имени')})")
        text = f"👑 Все карты:\n{cards_list}\n\n🔗 Привязанные:\n" + ("\n".join(bound) if bound else "  ❌ Нет привязанных")
    else:
        text = "❌ Нет доступных карт."
    await update.message.reply_text(text, parse_mode='HTML')


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    users = load_users()
    if users:
        lines = []
        for uid, info in users.items():
            cards = info.get('cards') or ([info.get('card_number')] if info.get('card_number') else [])
            cards_str = ", ".join(mask_card_number(c) for c in cards) if cards else "—"
            lines.append(f"👤 {uid} | Карты: {cards_str} | {info.get('name', 'без имени')}")
        text = "👑 Все пользователи:\n" + "\n".join(lines)
    else:
        text = "❌ Нет пользователей."
    await update.message.reply_text(text, parse_mode='HTML')


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return

    msg = await update.message.reply_text("🔄 Загрузка...")
    report_text = get_report_text()
    await msg.edit_text(report_text, parse_mode='HTML')


async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return

    msg = await update.message.reply_text("🔄 Формирую месячный отчет...")

    try:
        report_text = get_monthly_report_text()
        await msg.edit_text(report_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"❌ Ошибка принудительного месячного отчета: {e}")
        await msg.edit_text(f"❌ Ошибка: {e}")


async def my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    users = load_users()
    entry = users.get(user_id)
    cards = []
    if entry:
        cards = entry.get('cards') or ([entry.get('card_number')] if entry.get('card_number') else [])
        cards = [c for c in cards if c]
    if not cards:
        await update.message.reply_text(f"❌ Карта не привязана. Ваш ID: {user_id}")
        return

    msg = await update.message.reply_text("🔄 Загрузка...")
    parts = []
    for card_number in cards:
        parts.append(get_personal_report_text(card_number, 7))
    await msg.edit_text("\n\n➖➖➖➖➖\n\n".join(parts), parse_mode='HTML')


async def my30(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    users = load_users()
    entry = users.get(user_id)
    cards = []
    if entry:
        cards = entry.get('cards') or ([entry.get('card_number')] if entry.get('card_number') else [])
        cards = [c for c in cards if c]
    if not cards:
        await update.message.reply_text(f"❌ Карта не привязана. Ваш ID: {user_id}")
        return

    msg = await update.message.reply_text("🔄 Загрузка...")
    parts = []
    for card_number in cards:
        parts.append(get_monthly_report_text(card_number))
    await msg.edit_text("\n\n➖➖➖➖➖\n\n".join(parts), parse_mode='HTML')


# ================== ЗАДАЧИ ==================

async def refresh_data_job(context: ContextTypes.DEFAULT_TYPE):
    """Обновление данных — только по будням (ПН-ПТ)"""
    if is_weekend():
        logger.info("🗓️ Выходной — обновление данных пропущено")
        return

    logger.info("🔄 Обновление данных...")
    bot = context.bot if context else None

    success = refresh_data()
    if success:
        logger.info("✅ Данные успешно обновлены")
        mark_done("refresh_data")
        if bot:
            await send_log_to_topic(f"✅ Данные обновлены в {get_moscow_time().strftime('%H:%M')} (МСК)", bot)
    else:
        logger.error("❌ Ошибка обновления данных")
        if bot:
            await send_log_to_topic(f"❌ Ошибка обновления данных в {get_moscow_time().strftime('%H:%M')} (МСК)", bot)


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневный отчёт — только по будням (ПН-ПТ)"""
    if is_weekend():
        logger.info("🗓️ Выходной — ежедневный отчёт пропущен")
        return

    now = get_moscow_time()
    logger.info(f"📊 Ежедневный отчет в {now.strftime('%H:%M')} (МСК)")
    bot = context.bot

    try:
        report_text = get_report_text()
        ok = await send_with_retry(bot, ADMIN_CHAT_ID, report_text, parse_mode='HTML')
        if ok:
            mark_done("daily_report")
            logger.info("✅ Ежедневный отчет отправлен")
            await send_log_to_topic(f"✅ Ежедневный отчет отправлен в {now.strftime('%H:%M')} (МСК)", bot)
        else:
            logger.error("❌ Не удалось отправить ежедневный отчет")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ежедневного отчета: {e}")


async def monday_reports_job(context: ContextTypes.DEFAULT_TYPE):
    now = get_moscow_time()
    logger.info(f"📊 Еженедельные отчеты в {now.strftime('%H:%M')} (МСК)")
    bot = context.bot

    try:
        users = load_users()
        sent = 0
        for uid, data in users.items():
            cards = data.get('cards') or ([data.get('card_number')] if data.get('card_number') else [])
            cards = [c for c in cards if c]
            if not cards:
                continue
            try:
                parts = [get_personal_report_text(c, 7) for c in cards]
                text = "\n\n➖➖➖➖➖\n\n".join(parts)
                await send_with_retry(bot, int(uid), text, parse_mode='HTML')
                sent += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка отправки отчета пользователю {uid}: {e}")

        mark_done("monday_reports")
        logger.info(f"📊 Еженедельные отчеты: отправлено {sent}")
        await send_log_to_topic(f"📊 Еженедельные отчеты: отправлено {sent}", bot)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки еженедельных отчетов: {e}")


async def monthly_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_last_working_day_of_month():
        return

    now = get_moscow_time()
    logger.info("📊 Последний рабочий день месяца — отправляем месячные отчеты")
    bot = context.bot

    try:
        report_text = get_monthly_report_text()
        await send_with_retry(bot, ADMIN_CHAT_ID, report_text, parse_mode='HTML')

        users = load_users()
        sent = 0
        for uid, data in users.items():
            cards = data.get('cards') or ([data.get('card_number')] if data.get('card_number') else [])
            cards = [c for c in cards if c]
            if not cards:
                continue
            try:
                parts = [get_monthly_report_text(c) for c in cards]
                text = "\n\n➖➖➖➖➖\n\n".join(parts)
                await send_with_retry(bot, int(uid), text, parse_mode='HTML')
                sent += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка отправки месячного отчета {uid}: {e}")

        mark_done("monthly_reports")
        logger.info(f"📊 Месячные отчеты: отправлено {sent + 1}")
        await send_log_to_topic(f"📊 Месячные отчеты: отправлено {sent + 1}", bot)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки месячных отчетов: {e}")


# ============ СТОРОЖ (100% ГАРАНТИЯ) ============

async def catch_up_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Сторожевая задача: запускается каждую минуту.
    Проверяет только по будням (ПН-ПТ).
    """
    now = get_moscow_time()
    weekday = now.weekday()

    # В выходные ничего не делаем
    if weekday >= 5:
        return

    # --- Обновление данных (будни, после 6:00) ---
    if now.hour >= 6 and not is_done_today("refresh_data"):
        logger.warning("⚠️ catch_up: обновление данных пропущено — выполняю сейчас")
        try:
            await refresh_data_job(context)
        except Exception as e:
            logger.error(f"catch_up refresh_data error: {e}")

    # --- Ежедневный отчёт (будни, после 9:15) ---
    if now.hour > 9 or (now.hour == 9 and now.minute >= 15):
        if not is_done_today("daily_report"):
            logger.warning("⚠️ catch_up: ежедневный отчёт пропущен — отправляю сейчас")
            try:
                await daily_report_job(context)
            except Exception as e:
                logger.error(f"catch_up daily_report error: {e}")

    # --- Еженедельный отчёт (ПН, после 9:20) ---
    if weekday == 0 and (now.hour > 9 or (now.hour == 9 and now.minute >= 20)):
        if not is_done_today("monday_reports"):
            logger.warning("⚠️ catch_up: еженедельный отчёт пропущен — отправляю сейчас")
            try:
                await monday_reports_job(context)
            except Exception as e:
                logger.error(f"catch_up monday_reports error: {e}")

    # --- Месячный отчёт (последний рабочий день, после 9:19) ---
    if is_last_working_day_of_month() and (now.hour > 9 or (now.hour == 9 and now.minute >= 19)):
        if not is_done_today("monthly_reports"):
            logger.warning("⚠️ catch_up: месячный отчёт пропущен — отправляю сейчас")
            try:
                await monthly_check_job(context)
            except Exception as e:
                logger.error(f"catch_up monthly_reports error: {e}")


def setup_jobs(app: Application):
    """Настройка задач планировщика"""
    job_queue = app.job_queue
    if job_queue is None:
        logger.error("❌ JobQueue не инициализирован!")
        return False

    # Обновление данных — только ПН-ПТ
    job_queue.run_daily(
        refresh_data_job,
        time=dt.time(hour=6, minute=0, tzinfo=MOSCOW_TZ),
        days=tuple(range(5)),
        job_kwargs={'misfire_grace_time': 600, 'coalesce': True}
    )
    logger.info("⏰ Запланировано обновление данных в 6:00 (МСК) по будням")

    # Ежедневный отчёт — только ПН-ПТ
    job_queue.run_daily(
        daily_report_job,
        time=dt.time(hour=9, minute=15, tzinfo=MOSCOW_TZ),
        days=tuple(range(5)),
        job_kwargs={'misfire_grace_time': 600, 'coalesce': True}
    )
    logger.info("⏰ Запланирован ежедневный отчет в 9:15 (МСК) по будням")

    # Еженедельный отчёт — только ПН
    job_queue.run_daily(
        monday_reports_job,
        time=dt.time(hour=9, minute=20, tzinfo=MOSCOW_TZ),
        days=(0,),
        job_kwargs={'misfire_grace_time': 600, 'coalesce': True}
    )
    logger.info("⏰ Запланирован еженедельный отчет в понедельник 9:20 (МСК)")

    # Месячный отчёт — каждый день (проверка внутри is_last_working_day_of_month)
    job_queue.run_daily(
        monthly_check_job,
        time=dt.time(hour=9, minute=19, tzinfo=MOSCOW_TZ),
        job_kwargs={'misfire_grace_time': 600, 'coalesce': True}
    )
    logger.info("⏰ Запланирована проверка последнего рабочего дня в 9:19 (МСК)")

    # СТОРОЖ — каждую минуту (внутри проверяет выходные)
    job_queue.run_repeating(
        catch_up_job,
        interval=60,
        first=10,
        name="catch_up_job",
        job_kwargs={'misfire_grace_time': 120, 'coalesce': True}
    )
    logger.info("🛡️ Сторожевая задача catch_up_job запущена (каждые 60 сек, только будни)")

    jobs = job_queue.jobs()
    logger.info(f"✅ Всего запланировано задач: {len(jobs)}")
    for job in jobs:
        logger.info(f"  - {job.name}")

    return True


def main():
    logger.info("=" * 50)
    logger.info("🤖 ЗАПУСК БОТА")
    logger.info("=" * 50)

    if not db_helper.init_db():
        logger.error("❌ Критическая ошибка: не удалось инициализировать БД")
        sys.exit(1)

    logger.info("✅ База данных инициализирована")

    try:
        application = Application.builder() \
            .token(TOKEN) \
            .base_url(TG_API_URL) \
            .build()

        if USE_PROXY and PROXY_URL:
            logger.info(f"🌐 Используем SOCKS5 прокси: {PROXY_URL}")
            os.environ['ALL_PROXY'] = PROXY_URL
            os.environ['SOCKS5_PROXY'] = PROXY_URL
            os.environ['NO_PROXY'] = 'localhost,127.0.0.1,api.opti-24.ru'

        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, log_all_messages),
            group=0
        )
        application.add_handler(
            MessageHandler(filters.COMMAND, log_all_messages),
            group=0
        )

        application.add_handler(CommandHandler("start", start), group=1)
        application.add_handler(CommandHandler("help", help_command), group=1)
        application.add_handler(CommandHandler("adminhelp", adminhelp), group=1)
        application.add_handler(CommandHandler("bind", bind), group=1)
        application.add_handler(CommandHandler("unbind", unbind), group=1)
        application.add_handler(CommandHandler("cards", cards), group=1)
        application.add_handler(CommandHandler("users", users_command), group=1)
        application.add_handler(CommandHandler("report", report), group=1)
        application.add_handler(CommandHandler("monthly_report", monthly_report), group=1)
        application.add_handler(CommandHandler("my", my), group=1)
        application.add_handler(CommandHandler("my30", my30), group=1)

        logger.info("✅ Обработчики команд зарегистрированы")

        if application.job_queue:
            try:
                application.job_queue.scheduler.configure(
                    job_defaults={
                        'misfire_grace_time': 600,
                        'coalesce': True,
                        'max_instances': 1
                    }
                )
                logger.info("⚙️ JobQueue настроен: misfire_grace_time=600s")
            except Exception as e:
                logger.warning(f"Не удалось настроить job_defaults: {e}")

        setup_jobs(application)

        logger.info("🚀 Бот запущен и готов к работе")

        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
