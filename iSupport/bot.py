import logging
import re
import requests
import os
import time
import threading
import socket
import io
import shutil
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import config
from database import Database

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

PROXY_URL = "socks5://192.168.1.141:8085"
print(f"✅ Прокси настроен (только для Telegram Bot API): {PROXY_URL}")

db = Database()
user_states = {}
pending_files = {}
pending_file_links = {}
pending_categories = {}
pending_problem_category = {}
pending_users = {}
pinned_message_id = None

# ========== ОТПРАВКА ЛОГОВ ЧЕРЕЗ HTTP (ТЕМА 5) ==========
def send_log(message):
    """Отправляет лог в тему 5 через HTTP запрос"""
    try:
        if hasattr(config, 'LOG_CHAT_ID') and config.LOG_CHAT_ID:
            url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": config.LOG_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "message_thread_id": 5
            }
            proxies = {
                "http": "socks5://192.168.1.141:8085",
                "https": "socks5://192.168.1.141:8085"
            }
            response = requests.post(url, json=payload, timeout=10, proxies=proxies)
            if response.status_code != 200:
                logger.error(f"Ошибка отправки лога: {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка отправки лога: {e}")

# ========== ПОСТОЯННОЕ МЕНЮ ==========
def get_main_menu(is_admin=False):
    keyboard = [
        [KeyboardButton("📝 Новая задача")],
        [KeyboardButton("📋 Мои задачи")],
        [KeyboardButton("ℹ️ Помощь")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton("📋 Все задачи")])
        keyboard.append([KeyboardButton("📊 Статистика")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ========== ФУНКЦИИ ПРОВЕРКИ ФАЙЛОВ ==========
def validate_file(file, file_name):
    if file.file_size > config.MAX_FILE_SIZE:
        max_size_mb = config.MAX_FILE_SIZE / (1024 * 1024)
        return False, f"❌ Файл слишком большой. Максимальный размер: {max_size_mb:.0f} МБ"

    mime_type = file.mime_type or "application/octet-stream"
    ext = os.path.splitext(file_name)[1].lower() if file_name else ""

    if ext in config.BLOCKED_EXTENSIONS:
        return False, f"❌ Файлы с расширением {ext} запрещены"

    if mime_type in config.ALLOWED_FILE_TYPES:
        return True, "OK"

    if ext in config.ALLOWED_EXTENSIONS:
        return True, "OK"

    return False, f"❌ Файл '{file_name}' имеет неподдерживаемый формат."

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def format_time(dt):
    return dt.strftime("%H:%M")

def format_date(dt):
    return dt.strftime("%d.%m.%Y")

def get_status_emoji(status):
    emojis = {
        "Новое": "🆕",
        "На рассмотрении": "🔍",
        "В работе": "🛠️",
        "Исправлено": "✅",
        "Отклонено": "❌"
    }
    return emojis.get(status, "📌")

def get_task_display_text(task, show_id=True):
    """Формирует строку для отображения задачи с зачёркиванием для выполненных"""
    emoji = get_status_emoji(task['status'])
    task_id_str = f"#{task['id']} " if show_id else ""
    text_preview = task['text'][:40] + '...' if len(task['text']) > 40 else task['text']
    category = task['category']
    
    if task['status'] in ["Исправлено", "Отклонено"]:
        return f"{emoji} <s>{task_id_str}{category}</s>\n   <s>📝 {text_preview}</s>\n   📊 {task['status']}\n   🕐 {task.get('date', '')}"
    else:
        return f"{emoji} <b>{task_id_str}{category}</b>\n   📝 {text_preview}\n   📊 {task['status']}\n   🕐 {task.get('date', '')}"

def format_pinned_status():
    resources = db.get_resources()
    if not resources:
        return "❌ Нет данных о ресурсах."

    status_line = ""
    for res in resources[:4]:
        last_status = db.get_last_status(res["address"])
        icon = "🟢" if last_status == "OK" else "🔴" if last_status == "FAIL" else "⚪"
        status_line += f"{icon} {res['description']} "

    now = datetime.now()
    return f"{status_line.strip()}\n\n🕐 {format_date(now)} {format_time(now)}"

def update_pinned_status():
    global pinned_message_id
    try:
        if not config.STATUS_CHAT_ID:
            return

        status_text = format_pinned_status()

        if pinned_message_id:
            try:
                updater.bot.edit_message_text(
                    chat_id=config.STATUS_CHAT_ID,
                    message_id=pinned_message_id,
                    text=status_text,
                    parse_mode="Markdown"
                )
                return
            except:
                pinned_message_id = None

        msg = updater.bot.send_message(
            chat_id=config.STATUS_CHAT_ID,
            text=status_text,
            parse_mode="Markdown"
        )

        try:
            updater.bot.pin_chat_message(
                chat_id=config.STATUS_CHAT_ID,
                message_id=msg.message_id,
                disable_notification=True
            )
        except:
            pass

        pinned_message_id = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка закрепления: {e}")

def get_task_buttons(task_id):
    files = db.get_task_files(task_id)
    has_files = len(files) > 0

    buttons = [
        [
            InlineKeyboardButton("🛠️ В работе", callback_data=f"inprogress_{task_id}"),
            InlineKeyboardButton("✅ Исправлено", callback_data=f"resolve_{task_id}")
        ],
        [
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{task_id}"),
        ]
    ]

    if has_files:
        buttons[1].append(InlineKeyboardButton("📎 Файлы", callback_data=f"files_{task_id}"))
    else:
        buttons[1].append(InlineKeyboardButton("📭 Нет файлов", callback_data="no_files"))

    buttons.append([
        InlineKeyboardButton("💬 Комментарий", callback_data=f"comment_{task_id}"),
        InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")
    ])

    return InlineKeyboardMarkup(buttons)

# ========== ПРОВЕРКА НОВЫХ ПОЛЬЗОВАТЕЛЕЙ ==========
def check_new_user(user_id, username, full_name, chat_id):
    if db.is_admin(user_id):
        return True
    if db.is_user_exists(user_id):
        return True
    if str(user_id) in pending_users:
        return False

    admins = db.get_admin_list()
    if not admins:
        return False

    pending_users[str(user_id)] = {
        "username": username or full_name,
        "full_name": full_name,
        "chat_id": chat_id,
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
    }

    for admin in admins:
        try:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{user_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_user_{user_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            message = f"🆕 *ЗАПРОС НА ДОСТУП*\n\n"
            message += f"👤 *Пользователь:* {username or full_name}\n"
            message += f"🆔 *ID:* `{user_id}`\n"
            message += f"🕐 *Время:* {pending_users[str(user_id)]['timestamp']}\n\n"
            message += f"ℹ️ Пользователь хочет получить доступ к боту."

            updater.bot.send_message(
                chat_id=admin["id"],
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка отправки запроса админу {admin['id']}: {e}")

    return False

def approve_user_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    admin_id = update.effective_user.id

    if not db.is_admin(admin_id):
        query.edit_message_text("⛔ У вас нет прав для подтверждения пользователей")
        return

    data = query.data
    parts = data.split('_')
    user_id = int(parts[1])

    if str(user_id) not in pending_users:
        query.edit_message_text("❌ Запрос на подтверждение уже обработан")
        return

    user_info = pending_users[str(user_id)]
    username = user_info.get("username", f"User_{user_id}")
    chat_id = user_info.get("chat_id")

    try:
        db.add_user(str(user_id), username)
        query.edit_message_text(f"✅ Пользователь {username} подтвержден")
        send_log(f"👤 Пользователь {username} (ID: {user_id}) подтвержден администратором")

        try:
            message = f"✅ *Доступ к боту подтвержден!*\n\n"
            message += f"👤 Вы получили доступ к боту технической поддержки.\n"
            message += f"📋 Используйте /help для списка команд."
            context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

        del pending_users[str(user_id)]
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}")
        query.edit_message_text(f"❌ Ошибка: {e}")

def reject_user_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    admin_id = update.effective_user.id

    if not db.is_admin(admin_id):
        query.edit_message_text("⛔ У вас нет прав для отклонения пользователей")
        return

    data = query.data
    parts = data.split('_')
    user_id = int(parts[2]) if len(parts) > 2 else int(parts[1])

    if str(user_id) not in pending_users:
        query.edit_message_text("❌ Запрос уже обработан")
        return

    user_info = pending_users[str(user_id)]
    username = user_info.get("username", f"User_{user_id}")
    chat_id = user_info.get("chat_id")

    query.edit_message_text(f"❌ Пользователь {username} отклонен")

    try:
        message = f"❌ *Доступ к боту отклонен*\n\n"
        message += f"К сожалению, администратор отклонил ваш запрос."
        context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    del pending_users[str(user_id)]

# ========== КОМАНДЫ ==========
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    full_name = update.effective_user.full_name or ""
    chat_id = update.effective_chat.id

    send_log(f"👤 {username or full_name} (ID: {user_id}) запустил бота")

    is_admin = db.is_admin(user_id)
    is_user = db.is_user_exists(user_id)

    if not is_admin and not is_user:
        response = check_new_user(user_id, username, full_name, chat_id)
        if not response:
            update.message.reply_text(
                "🔄 *Запрос на доступ отправлен администраторам*\n\n"
                "Дождитесь подтверждения.",
                reply_markup=get_main_menu(False)
            )
        return

    send_start_menu(update, context, is_admin)

def help_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    is_admin = db.is_admin(user_id)
    send_log(f"👤 {update.effective_user.username or update.effective_user.full_name} (ID: {user_id}) открыл помощь")
    send_start_menu(update, context, is_admin)

def send_start_menu(update: Update, context: CallbackContext, is_admin):
    menu = "🤖 *Бот технической поддержки*\n\n"

    menu += "*📊 МОНИТОРИНГ РЕСУРСОВ:*\n"
    menu += "/check - 🔍 Проверить все ресурсы\n"
    menu += "/status - 📊 Текущий статус ресурсов\n"
    menu += "/logs - 📜 Последние 10 проверок\n"
    menu += "/updatepin - 📌 Обновить закрепленный статус\n\n"

    menu += "*📝 TASK TRACKER (ЗАДАЧИ):*\n"
    menu += "/new --- ➕ Создать задачу\n"
    menu += "/my --- 📋 Мои задачи\n"
    menu += "/status [номер] --- ℹ️ Статус задачи\n"
    menu += "/history [номер] --- 📜 История задачи\n"
    menu += "/files [номер] --- 📎 Получить файлы задачи\n"
    menu += "/cancel --- ❌ Отменить создание задачи\n\n"

    menu += "💡 *Или просто напишите текст* --- бот предложит выбрать категорию\n\n"

    menu += "*📎 ФАЙЛЫ:*\n"
    menu += f"Максимальный размер: {config.MAX_FILE_SIZE / (1024*1024):.0f} МБ\n"
    menu += f"Разрешенные форматы: {', '.join(list(config.ALLOWED_EXTENSIONS)[:8])}...\n"
    menu += "📌 Файлы хранятся на сервере\n\n"

    if is_admin:
        menu += "*👑 АДМИН КОМАНДЫ:*\n"
        menu += "/tasks --- 📋 Все задачи\n"
        menu += "/taskstats --- 📊 Статистика\n"
        menu += "/inprogress [номер] --- ⚙️ В работе\n"
        menu += "/resolve [номер] --- ✅ Исправлено\n"
        menu += "/reject [номер] [причина] --- ❌ Отклонить с причиной\n"
        menu += "/comment [номер] [текст] --- 💬 Комментарий\n"
        menu += "/deletefiles [номер] --- 🗑️ Удалить файлы задачи\n\n"

    menu += "📌 *Закрепленное сообщение* обновляется автоматически\n"
    menu += "⏰ *Автопроверка:* каждые 15 минут\n\n"
    menu += "📞 *По всем вопросам:* администратор"

    update.message.reply_text(menu, parse_mode="Markdown", reply_markup=get_main_menu(is_admin))

def check_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    send_log(f"👤 Админ {update.effective_user.username or update.effective_user.full_name} запустил проверку ресурсов")
    update.message.reply_text("🔍 Проверка ресурсов...")
    check_all_resources(notify=False)
    update.message.reply_text("✅ Проверка завершена!")

def status_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    resources = db.get_resources()
    if not resources:
        update.message.reply_text("❌ Нет данных о ресурсах.")
        return

    status_text = "*📊 СТАТУС ВСЕХ РЕСУРСОВ*\n\n"
    for res in resources:
        last_status = db.get_last_status(res["address"])
        emoji = "🟢" if last_status == "OK" else "🔴" if last_status == "FAIL" else "⚪"
        status_text += f"{emoji} *{res['description']}*\n"
        status_text += f" 📡 {res['address']} ({res['type'].upper()}) - {last_status}\n\n"

    update.message.reply_text(status_text, parse_mode="Markdown")

def logs_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    try:
        logs = db.get_logs(10)
        if not logs:
            update.message.reply_text("❌ Нет логов.")
            return

        log_text = "*📋 ПОСЛЕДНИЕ 10 ПРОВЕРОК*\n\n"
        for log in logs:
            emoji = "✅" if log["status"] == "OK" else "❌"
            log_text += f"{emoji} *{log['resource']}*\n"
            log_text += f" Статус: {log['status']}\n"
            log_text += f" Время: {log['time']}\n\n"

        update.message.reply_text(log_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка логов: {e}")
        update.message.reply_text("❌ Ошибка получения логов")

def updatepin_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    update_pinned_status()
    update.message.reply_text("📌 Закрепленное сообщение обновлено!")

# ========== TASK TRACKER ==========
def new_task(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_user_exists(user_id) and not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    chat_id = update.effective_chat.id

    problem_categories = db.get_problem_categories()
    if not problem_categories:
        show_task_type_selection(update, context)
        return

    keyboard = []
    for cat in problem_categories:
        keyboard.append([InlineKeyboardButton(cat, callback_data=f"prob_{cat}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_all")])

    update.message.reply_text(
        "📝 *Выберите категорию проблемы:*\n\n"
        "Это первый уровень категоризации",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    user_states[chat_id] = "waiting_problem_category"

def show_task_type_selection(update, context, chat_id=None):
    if chat_id is None:
        chat_id = update.effective_chat.id

    keyboard = []
    for i, (name, key) in enumerate(config.TASK_CATEGORIES.items(), 1):
        keyboard.append([InlineKeyboardButton(f"{i}. {name}", callback_data=f"type_{key}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_all")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        query = update.callback_query
        query.edit_message_text(
            "*📝 Выберите тип задачи:*\n\n"
            "Это второй уровень категоризации",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        update.message.reply_text(
            "*📝 Выберите тип задачи:*\n\n"
            "Это второй уровень категоризации",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    user_states[chat_id] = "waiting_task_type"

def problem_category_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data == "cancel_all":
        user_states.pop(chat_id, None)
        pending_categories.pop(chat_id, None)
        pending_problem_category.pop(chat_id, None)
        pending_files.pop(chat_id, None)
        pending_file_links.pop(chat_id, None)
        query.edit_message_text("❌ Создание задачи отменено")
        return

    if data.startswith("prob_"):
        problem_category = data.replace("prob_", "")
        pending_problem_category[chat_id] = problem_category
        show_task_type_selection(update, context, chat_id)

def task_type_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data == "cancel_all":
        user_states.pop(chat_id, None)
        pending_categories.pop(chat_id, None)
        pending_problem_category.pop(chat_id, None)
        pending_files.pop(chat_id, None)
        pending_file_links.pop(chat_id, None)
        query.edit_message_text("❌ Создание задачи отменено")
        return

    if data.startswith("type_"):
        task_type = data.replace("type_", "")
        pending_categories[chat_id] = task_type
        user_states[chat_id] = "waiting_text"

        problem_category = pending_problem_category.get(chat_id, "")
        category_name = config.CATEGORY_NAMES.get(task_type, task_type)

        message = f"✅ Категория выбрана: *{problem_category} → {category_name}*\n\n" if problem_category else f"✅ Категория выбрана: *{category_name}*\n\n"
        message += f"📝 Теперь напишите текст вашей задачи.\n"
        message += f"📎 Если нужно прикрепить файлы, отправьте их (макс. {config.MAX_FILE_SIZE / (1024*1024):.0f} МБ)\n"
        message += f" Разрешенные форматы: {', '.join(list(config.ALLOWED_EXTENSIONS)[:5])}...\n\n"
        message += f"📌 Файлы хранятся на сервере\n"
        message += f"Для отмены отправьте /cancel"

        query.edit_message_text(message, parse_mode="Markdown")

def cancel_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_states.pop(chat_id, None)
    pending_categories.pop(chat_id, None)
    pending_problem_category.pop(chat_id, None)
    pending_files.pop(chat_id, None)
    pending_file_links.pop(chat_id, None)
    update.message.reply_text("❌ Создание задачи отменено")

def history_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text = update.message.text
    match = re.search(r'/history\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /history [номер задачи]")
        return

    task_id = int(match.group(1))
    task = db.get_task(task_id)

    if not task:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")
        return

    if task["user_id"] != user_id and not db.is_admin(user_id):
        update.message.reply_text("⛔ У вас нет доступа к этой задаче")
        return

    message = f"📜 *ИСТОРИЯ ЗАДАЧИ #{task['id']}*\n\n"
    message += f"📝 *Текст:* {task['text']}\n"
    message += f"📊 *Текущий статус:* {task['status']}\n\n"
    message += "*Изменения:*\n"
    message += task['history']

    if len(message) > 4000:
        parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for part in parts:
            update.message.reply_text(part, parse_mode="Markdown")
    else:
        update.message.reply_text(message, parse_mode="Markdown")

def files_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text = update.message.text
    match = re.search(r'/files\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /files [номер задачи]")
        return

    task_id = int(match.group(1))
    task = db.get_task(task_id)

    if not task:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")
        return

    if task["user_id"] != user_id and not db.is_admin(user_id):
        update.message.reply_text("⛔ У вас нет доступа к этой задаче")
        return

    files = db.get_task_files(task_id)
    if not files:
        update.message.reply_text(f"📭 У задачи #{task_id} нет файлов")
        return

    for f in files:
        try:
            with open(f['path'], 'rb') as file_obj:
                caption = f"📎 Файл: {f['name']}\n📏 {f['size'] / 1024:.1f} KB"
                update.message.reply_document(
                    document=InputFile(file_obj, filename=f['name']),
                    caption=caption
                )
        except Exception as e:
            logger.error(f"Ошибка отправки файла {f['name']}: {e}")
            update.message.reply_text(f"❌ Ошибка отправки файла: {f['name']}")

def deletefiles_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    text = update.message.text
    match = re.search(r'/deletefiles\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /deletefiles [номер задачи]")
        return

    task_id = int(match.group(1))
    task = db.get_task(task_id)

    if not task:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")
        return

    files = db.get_task_files(task_id)
    if not files:
        update.message.reply_text(f"📭 У задачи #{task_id} нет файлов")
        return

    if db.delete_task_files(task_id):
        update.message.reply_text(f"✅ Файлы задачи #{task_id} удалены")
    else:
        update.message.reply_text(f"❌ Ошибка удаления файлов задачи #{task_id}")

# ========== АДМИН КОМАНДЫ (текстовые) ==========
def inprogress_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    text = update.message.text
    match = re.search(r'/inprogress\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /inprogress [номер задачи]")
        return

    task_id = int(match.group(1))
    admin_id = update.effective_user.id
    admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{admin_id}"

    user_id, success = db.change_task_status(task_id, "В работе", admin_id, admin_name)
    if success:
        update.message.reply_text(f"✅ Задача #{task_id} взята в работу")
        send_log(f"🛠️ {admin_name} взял задачу #{task_id} в работу")
        notify_user_status_change(task_id, "В работе", user_id, admin_name, context, reason=None)
    else:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")

def resolve_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    text = update.message.text
    match = re.search(r'/resolve\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /resolve [номер задачи]")
        return

    task_id = int(match.group(1))
    admin_id = update.effective_user.id
    admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{admin_id}"

    user_id, success = db.change_task_status(task_id, "Исправлено", admin_id, admin_name)
    if success:
        update.message.reply_text(f"✅ Задача #{task_id} отмечена как исправленная")
        send_log(f"✅ {admin_name} отметил задачу #{task_id} как исправленную")
        notify_user_status_change(task_id, "Исправлено", user_id, admin_name, context, reason=None)
    else:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")

def reject_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    text = update.message.text
    match = re.search(r'/reject\s+(\d+)(?:\s+(.+))?', text)

    if not match:
        update.message.reply_text("❌ Используйте: /reject [номер задачи] [причина]")
        return

    task_id = int(match.group(1))
    reason = match.group(2) if match.group(2) else "Причина не указана"

    admin_id = update.effective_user.id
    admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{admin_id}"

    user_id, success = db.change_task_status(task_id, "Отклонено", admin_id, admin_name)
    if success:
        db.add_comment(task_id, f"❌ ОТКЛОНЕНО: {reason}", admin_id, admin_name)
        update.message.reply_text(f"✅ Задача #{task_id} отклонена\n📝 Причина: {reason}")
        send_log(f"❌ {admin_name} отклонил задачу #{task_id} (причина: {reason})")
        notify_user_status_change(task_id, "Отклонено", user_id, admin_name, context, reason)
    else:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")

def comment_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    text = update.message.text
    match = re.search(r'/comment\s+(\d+)\s+(.+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /comment [номер задачи] [текст]")
        return

    task_id = int(match.group(1))
    comment = match.group(2)
    admin_id = update.effective_user.id
    admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{admin_id}"

    user_id, success = db.add_comment(task_id, comment, admin_id, admin_name)
    if success:
        update.message.reply_text(f"✅ Комментарий добавлен к задаче #{task_id}")
        send_log(f"💬 {admin_name} добавил комментарий к задаче #{task_id}: {comment}")
        notify_user_comment(task_id, comment, user_id, admin_name, context)
    else:
        update.message.reply_text(f"❌ Задача #{task_id} не найдена")

def notify_user_status_change(task_id, new_status, user_id, admin_name, context, reason=None):
    try:
        task = db.get_task(task_id)
        if not task:
            return

        status_icon = "✅" if new_status == "Исправлено" else "❌" if new_status == "Отклонено" else "🛠️" if new_status == "В работе" else "🆕"

        message = f"📌 *Обновление статуса задачи #{task_id}*\n\n"
        message += f"🏷️ *Категория:* {task['category']}\n"
        message += f"📝 *Текст:* {task['text'][:200]}{'...' if len(task['text']) > 200 else ''}\n"
        message += f"📊 *Новый статус:* {status_icon} {new_status}\n"
        message += f"👤 *Изменил:* {admin_name}\n"
        message += f"🕐 *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"

        if reason and new_status == "Отклонено":
            message += f"\n📝 *Причина отклонения:* {reason}\n"

        message += f"\n📎 /files {task_id} - посмотреть файлы"
        message += f"\n📜 /history {task_id} - посмотреть историю"

        context.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

def notify_user_comment(task_id, comment, user_id, admin_name, context):
    try:
        task = db.get_task(task_id)
        if not task:
            return

        message = f"💬 *Новый комментарий к задаче #{task_id}*\n\n"
        message += f"📝 *Текст задачи:* {task['text'][:100]}{'...' if len(task['text']) > 100 else ''}\n"
        message += f"💬 *Комментарий:* {comment}\n"
        message += f"👤 *Добавил:* {admin_name}\n"
        message += f"🕐 *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        message += f"\n📎 /files {task_id} - посмотреть файлы"
        message += f"\n📜 /history {task_id} - посмотреть историю"

        context.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

# ========== ВЫВОД ЗАДАЧ СПИСКОМ ==========
def show_tasks_list(update, context, tasks, title="📋 СПИСОК ЗАДАЧ", show_completed=True):
    if not tasks:
        update.message.reply_text("📭 Нет задач")
        return

    if not show_completed:
        tasks = [t for t in tasks if t['status'] not in ["Исправлено", "Отклонено"]]

    tasks_sorted = sorted(tasks, key=lambda x: x['id'])

    if not tasks_sorted:
        update.message.reply_text("📭 Нет активных задач")
        return

    message = f"<b>{title}</b>\n\n"
    if not show_completed:
        message += "📌 <b>Показаны только активные задачи</b>\n\n"

    for task in tasks_sorted[:20]:
        message += get_task_display_text(task, show_id=True) + "\n\n"

    if len(tasks_sorted) > 20:
        message += f"📊 <b>Показано 20 из {len(tasks_sorted)} задач</b>\n"

    keyboard = []
    row = []
    for i, task in enumerate(tasks_sorted[:20]):
        row.append(InlineKeyboardButton(f"#{task['id']}", callback_data=f"view_{task['id']}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    filter_text = "🔽 Скрыть выполненные" if show_completed else "🔼 Показать все"
    keyboard.append([InlineKeyboardButton(filter_text, callback_data="toggle_completed")])
    keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_list")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(message, parse_mode="HTML", reply_markup=reply_markup)

def my_tasks(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not db.is_user_exists(user_id) and not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    send_log(f"📋 {update.effective_user.username or update.effective_user.full_name} посмотрел свои задачи")
    tasks = db.get_user_tasks(user_id, limit=100)
    show_tasks_list(update, context, tasks, "📋 МОИ ЗАДАЧИ")

def tasks_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    send_log(f"📋 Админ {update.effective_user.username or update.effective_user.full_name} посмотрел все задачи")
    tasks = db.get_all_tasks(limit=100)
    show_tasks_list(update, context, tasks, "📋 ВСЕ ЗАДАЧИ")

def task_stats_command(update: Update, context: CallbackContext):
    if not db.is_admin(update.effective_user.id):
        update.message.reply_text("⛔ Нет доступа")
        return

    stats, categories = db.get_task_stats()
    period_info = db.get_stats_period()

    if not stats:
        update.message.reply_text("📭 Нет данных для статистики")
        return

    message = "*📊 СТАТИСТИКА ЗАДАЧ*\n\n"

    if period_info:
        message += f"📅 *Период:* {period_info['first_date']} — {period_info['last_date']}\n"
        message += f"📊 *Всего за период:* {period_info['total']} задач\n\n"

    message += "*По статусам:*\n"
    for status, count in stats.items():
        emoji = get_status_emoji(status)
        message += f"{emoji} {status}: {count}\n"

    if categories:
        message += "\n*По категориям:*\n"
        for category, count in categories.items():
            message += f"🏷️ {category}: {count}\n"

    total = sum(stats.values())
    message += f"\n📊 *Всего задач:* {total}"

    update.message.reply_text(message, parse_mode="Markdown")

def task_status(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text = update.message.text
    match = re.search(r'/status\s+(\d+)', text)

    if not match:
        update.message.reply_text("❌ Используйте: /status [номер задачи]")
        return

    task_id = int(match.group(1))
    show_task_detail(update, context, task_id, user_id)

def show_task_detail(update, context, task_id, user_id):
    task = db.get_task(task_id)

    if not task:
        if hasattr(update, 'message') and update.message:
            update.message.reply_text(f"❌ Задача #{task_id} не найдена")
        return

    if task["user_id"] != user_id and not db.is_admin(user_id):
        if hasattr(update, 'message') and update.message:
            update.message.reply_text("⛔ У вас нет доступа к этой задаче")
        return

    message = f"📌 *ЗАДАЧА #{task['id']}*\n\n"
    message += f"👤 *От:* {task['username']}\n"
    message += f"🏷️ *Категория:* {task['category']}\n"
    message += f"📝 *Текст:* {task['text']}\n"
    message += f"📊 *Статус:* {get_status_emoji(task['status'])} {task['status']}\n"
    message += f"🕐 *Создана:* {task['date']}\n"

    if task['files']:
        message += f"📎 *Файлы:*\n{task['files']}\n"

    if task['comment']:
        message += f"💬 *Комментарий:* {task['comment']}\n"

    reply_markup = get_task_buttons(task_id)

    if hasattr(update, 'message') and update.message:
        update.message.reply_text(message, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        query = update.callback_query
        query.edit_message_text(message, parse_mode="Markdown", reply_markup=reply_markup)

# ========== УВЕДОМЛЕНИЕ О НОВОЙ ЗАДАЧЕ ==========
def notify_admins_new_task(task_id, user_id, username, text, category, problem_category, files, file_links):
    admins = db.get_admin_list()
    category_name = config.CATEGORY_NAMES.get(category, "Другое")
    full_category = f"{problem_category} → {category_name}" if problem_category else category_name

    message = f"📩 *НОВАЯ ЗАДАЧА #{task_id}*\n\n"
    message += f"👤 *От:* {username}\n"
    message += f"🆔 *ID:* `{user_id}`\n"
    message += f"🏷️ *Категория:* {full_category}\n"
    message += f"📝 *Текст:* {text}\n"

    if files:
        message += f"📎 *Файлов:* {len(files)} файлов\n"

    message += f"🕐 *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"

    send_log(f"📩 Новая задача #{task_id} от {username}\n📝 {text[:100]}...")

    reply_markup = get_task_buttons(task_id)

    for admin in admins:
        try:
            updater.bot.send_message(
                chat_id=admin["id"],
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin['id']}: {e}")

# ========== ОБРАБОТЧИК INLINE КНОПОК ==========
show_completed_filter = {}

def button_callback(update: Update, context: CallbackContext):
    global show_completed_filter
    query = update.callback_query
    query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "no_files":
        query.edit_message_text("📭 У этой задачи нет файлов")
        return

    if data == "toggle_completed":
        current = show_completed_filter.get(user_id, True)
        show_completed_filter[user_id] = not current

        if db.is_admin(user_id):
            tasks = db.get_all_tasks(limit=100)
            title = "📋 ВСЕ ЗАДАЧИ"
        else:
            tasks = db.get_user_tasks(user_id, limit=100)
            title = "📋 МОИ ЗАДАЧИ"

        if not tasks:
            query.edit_message_text("📭 Нет задач")
            return

        tasks_sorted = sorted(tasks, key=lambda x: x['id'])

        if not show_completed_filter[user_id]:
            tasks_sorted = [t for t in tasks_sorted if t['status'] not in ["Исправлено", "Отклонено"]]

        if not tasks_sorted:
            query.edit_message_text("📭 Нет активных задач")
            return

        message = f"<b>{title}</b>\n\n"
        if not show_completed_filter[user_id]:
            message += "📌 <b>Показаны только активные задачи</b>\n\n"

        for task in tasks_sorted[:20]:
            message += get_task_display_text(task, show_id=True) + "\n\n"

        if len(tasks_sorted) > 20:
            message += f"📊 <b>Показано 20 из {len(tasks_sorted)} задач</b>\n"

        keyboard = []
        row = []
        for i, task in enumerate(tasks_sorted[:20]):
            row.append(InlineKeyboardButton(f"#{task['id']}", callback_data=f"view_{task['id']}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        filter_text = "🔽 Скрыть выполненные" if show_completed_filter[user_id] else "🔼 Показать все"
        keyboard.append([InlineKeyboardButton(filter_text, callback_data="toggle_completed")])
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_list")])

        query.edit_message_text(message, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "back_to_list":
        if db.is_admin(user_id):
            tasks = db.get_all_tasks(limit=100)
            title = "📋 ВСЕ ЗАДАЧИ"
        else:
            tasks = db.get_user_tasks(user_id, limit=100)
            title = "📋 МОИ ЗАДАЧИ"

        if not tasks:
            query.edit_message_text("📭 Нет задач")
            return

        tasks_sorted = sorted(tasks, key=lambda x: x['id'])

        show_comp = show_completed_filter.get(user_id, True)
        if not show_comp:
            tasks_sorted = [t for t in tasks_sorted if t['status'] not in ["Исправлено", "Отклонено"]]

        if not tasks_sorted:
            query.edit_message_text("📭 Нет активных задач")
            return

        message = f"<b>{title}</b>\n\n"
        if not show_comp:
            message += "📌 <b>Показаны только активные задачи</b>\n\n"

        for task in tasks_sorted[:20]:
            message += get_task_display_text(task, show_id=True) + "\n\n"

        if len(tasks_sorted) > 20:
            message += f"📊 <b>Показано 20 из {len(tasks_sorted)} задач</b>\n"

        keyboard = []
        row = []
        for i, task in enumerate(tasks_sorted[:20]):
            row.append(InlineKeyboardButton(f"#{task['id']}", callback_data=f"view_{task['id']}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        filter_text = "🔽 Скрыть выполненные" if show_comp else "🔼 Показать все"
        keyboard.append([InlineKeyboardButton(filter_text, callback_data="toggle_completed")])
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_list")])

        query.edit_message_text(message, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "refresh_list":
        if db.is_admin(user_id):
            tasks = db.get_all_tasks(limit=100)
            title = "📋 ВСЕ ЗАДАЧИ"
        else:
            tasks = db.get_user_tasks(user_id, limit=100)
            title = "📋 МОИ ЗАДАЧИ"

        if not tasks:
            query.edit_message_text("📭 Нет задач")
            return

        tasks_sorted = sorted(tasks, key=lambda x: x['id'])

        show_comp = show_completed_filter.get(user_id, True)
        if not show_comp:
            tasks_sorted = [t for t in tasks_sorted if t['status'] not in ["Исправлено", "Отклонено"]]

        if not tasks_sorted:
            query.edit_message_text("📭 Нет активных задач")
            return

        message = f"<b>{title}</b>\n\n"
        if not show_comp:
            message += "📌 <b>Показаны только активные задачи</b>\n\n"

        for task in tasks_sorted[:20]:
            message += get_task_display_text(task, show_id=True) + "\n\n"

        if len(tasks_sorted) > 20:
            message += f"📊 <b>Показано 20 из {len(tasks_sorted)} задач</b>\n"

        keyboard = []
        row = []
        for i, task in enumerate(tasks_sorted[:20]):
            row.append(InlineKeyboardButton(f"#{task['id']}", callback_data=f"view_{task['id']}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        filter_text = "🔽 Скрыть выполненные" if show_comp else "🔼 Показать все"
        keyboard.append([InlineKeyboardButton(filter_text, callback_data="toggle_completed")])
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_list")])

        query.edit_message_text(message, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("view_"):
        task_id = int(data.replace("view_", ""))
        task = db.get_task(task_id)

        if not task:
            query.edit_message_text(f"❌ Задача #{task_id} не найдена")
            return

        if task["user_id"] != user_id and not db.is_admin(user_id):
            query.edit_message_text("⛔ У вас нет доступа к этой задаче")
            return

        message = f"📌 *ЗАДАЧА #{task['id']}*\n\n"
        message += f"👤 *От:* {task['username']}\n"
        message += f"🏷️ *Категория:* {task['category']}\n"
        message += f"📝 *Текст:* {task['text']}\n"
        message += f"📊 *Статус:* {get_status_emoji(task['status'])} {task['status']}\n"
        message += f"🕐 *Создана:* {task['date']}\n"

        if task['files']:
            message += f"📎 *Файлы:*\n{task['files']}\n"

        if task['comment']:
            message += f"💬 *Комментарий:* {task['comment']}\n"

        reply_markup = get_task_buttons(task_id)
        query.edit_message_text(message, parse_mode="Markdown", reply_markup=reply_markup)
        return

    if not db.is_admin(user_id):
        query.edit_message_text("⛔ У вас нет прав для выполнения этого действия")
        return

    parts = data.split('_')
    action = parts[0]
    task_id = int(parts[1])

    task = db.get_task(task_id)
    if not task:
        query.edit_message_text(f"❌ Задача #{task_id} не найдена")
        return

    admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{user_id}"

    if action == "inprogress":
        user_id, success = db.change_task_status(task_id, "В работе", user_id, admin_name)
        if success:
            query.edit_message_text(f"✅ Задача #{task_id} взята в работу")
            send_log(f"🛠️ {admin_name} взял задачу #{task_id} в работу (через кнопку)")
            reply_markup = get_task_buttons(task_id)
            query.message.edit_reply_markup(reply_markup=reply_markup)
            notify_user_status_change(task_id, "В работе", user_id, admin_name, context, reason=None)

    elif action == "resolve":
        user_id, success = db.change_task_status(task_id, "Исправлено", user_id, admin_name)
        if success:
            query.edit_message_text(f"✅ Задача #{task_id} отмечена как исправленная")
            send_log(f"✅ {admin_name} отметил задачу #{task_id} как исправленную (через кнопку)")
            reply_markup = get_task_buttons(task_id)
            query.message.edit_reply_markup(reply_markup=reply_markup)
            notify_user_status_change(task_id, "Исправлено", user_id, admin_name, context, reason=None)

    elif action == "reject":
        query.edit_message_text(
            f"❌ Введите причину отклонения для задачи #{task_id}\n\n"
            f"Просто отправьте текст с причиной"
        )
        context.user_data['pending_reject_task'] = task_id

    elif action == "files":
        files = db.get_task_files(task_id)
        if not files:
            query.edit_message_text(f"📭 У задачи #{task_id} нет файлов")
            return

        query.edit_message_text(f"📎 Отправляю файлы задачи #{task_id}...")
        for f in files:
            try:
                with open(f['path'], 'rb') as file_obj:
                    caption = f"📎 {f['name']} ({f['size'] / 1024:.1f} KB)"
                    context.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(file_obj, filename=f['name']),
                        caption=caption
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки файла {f['name']}: {e}")

    elif action == "comment":
        query.edit_message_text(
            f"💬 Введите текст комментария для задачи #{task_id}\n\n"
            f"Просто отправьте текст"
        )
        context.user_data['pending_comment_task'] = task_id

# ========== ОБРАБОТЧИК КОММЕНТАРИЕВ И ПРИЧИН ОТКЛОНЕНИЯ ==========
def handle_comment_text(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text

    if context.user_data.get('pending_comment_task'):
        task_id = context.user_data['pending_comment_task']

        if not db.is_admin(user_id):
            update.message.reply_text("⛔ Нет прав для добавления комментария")
            context.user_data['pending_comment_task'] = None
            return

        admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{user_id}"
        user_id_task, success = db.add_comment(task_id, text, user_id, admin_name)

        if success:
            update.message.reply_text(f"✅ Комментарий добавлен к задаче #{task_id}")
            send_log(f"💬 {admin_name} добавил комментарий к задаче #{task_id}: {text}")
            notify_user_comment(task_id, text, user_id_task, admin_name, context)
        else:
            update.message.reply_text(f"❌ Задача #{task_id} не найдена")

        context.user_data['pending_comment_task'] = None
        return

    if context.user_data.get('pending_reject_task'):
        task_id = context.user_data['pending_reject_task']
        reason = text

        if not db.is_admin(user_id):
            update.message.reply_text("⛔ Нет прав для отклонения задачи")
            context.user_data['pending_reject_task'] = None
            return

        admin_name = update.effective_user.username or update.effective_user.full_name or f"Admin_{user_id}"

        user_id_task, success = db.change_task_status(task_id, "Отклонено", user_id, admin_name)
        if success:
            db.add_comment(task_id, f"❌ ОТКЛОНЕНО: {reason}", user_id, admin_name)
            update.message.reply_text(f"✅ Задача #{task_id} отклонена\n📝 Причина: {reason}")
            send_log(f"❌ {admin_name} отклонил задачу #{task_id} (причина: {reason})")
            notify_user_status_change(task_id, "Отклонено", user_id_task, admin_name, context, reason)
        else:
            update.message.reply_text(f"❌ Задача #{task_id} не найдена")

        context.user_data['pending_reject_task'] = None
        return

def handle_text(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text
    username = update.effective_user.username or update.effective_user.full_name
    is_admin = db.is_admin(user_id)

    if text == "📝 Новая задача":
        new_task(update, context)
        return
    elif text == "📋 Мои задачи":
        my_tasks(update, context)
        return
    elif text == "📋 Все задачи":
        if is_admin:
            tasks_command(update, context)
        else:
            update.message.reply_text("⛔ Нет доступа")
        return
    elif text == "📊 Статистика":
        if is_admin:
            task_stats_command(update, context)
        else:
            update.message.reply_text("⛔ Нет доступа")
        return
    elif text == "ℹ️ Помощь":
        help_command(update, context)
        return

    if context.user_data.get('pending_comment_task') or context.user_data.get('pending_reject_task'):
        handle_comment_text(update, context)
        return

    if not db.is_user_exists(user_id) and not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа. Обратитесь к администратору.")
        return

    if user_states.get(chat_id) == "waiting_text":
        category = pending_categories.get(chat_id)
        problem_category = pending_problem_category.get(chat_id, "")

        if category:
            files = pending_files.get(chat_id, [])
            file_links = pending_file_links.get(chat_id, [])

            task_id = db.create_task(user_id, username, text, category, problem_category, files, file_links)

            if task_id:
                response = f"✅ *Задача #{task_id} создана!*\n\n"
                response += f"🏷️ *Категория:* {pending_problem_category.get(chat_id, '')} → {config.CATEGORY_NAMES.get(category, category)}\n" if pending_problem_category.get(chat_id) else f"🏷️ *Категория:* {config.CATEGORY_NAMES.get(category, category)}\n"
                response += f"📝 *Текст:* {text}\n"
                response += f"📊 *Статус:* 🆕 Новое\n"
                response += f"🕐 *Создано:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"

                if file_links:
                    task_folder = os.path.join(config.FILES_STORAGE_PATH, f"task_{task_id}")
                    os.makedirs(task_folder, exist_ok=True)
                    for i, file_path in enumerate(file_links):
                        if os.path.exists(file_path):
                            new_path = os.path.join(task_folder, files[i] if i < len(files) else os.path.basename(file_path))
                            shutil.move(file_path, new_path)
                    response += f"\n📎 *Файлов:* {len(file_links)} сохранено на сервере\n"

                send_log(f"📝 {username} создал задачу #{task_id}: {text[:100]}...")
                update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_main_menu(is_admin))
                notify_admins_new_task(task_id, user_id, username, text, category, problem_category, files, file_links)
            else:
                update.message.reply_text("❌ Ошибка при создании задачи")

            user_states.pop(chat_id, None)
            pending_categories.pop(chat_id, None)
            pending_problem_category.pop(chat_id, None)
            pending_files.pop(chat_id, None)
            pending_file_links.pop(chat_id, None)
        else:
            update.message.reply_text("❌ Ошибка: не выбрана категория")
        return

    if not text.startswith("/"):
        problem_categories = db.get_problem_categories()
        if problem_categories:
            keyboard = []
            for cat in problem_categories:
                keyboard.append([InlineKeyboardButton(cat, callback_data=f"prob_{cat}")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_all")])

            update.message.reply_text(
                "📝 *Чтобы создать задачу, выберите категорию проблемы:*\n\n"
                "Или отправьте /new",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            user_states[chat_id] = "waiting_problem_category"
        else:
            show_task_type_selection(update, context, chat_id)
        return

    update.message.reply_text("❌ Неизвестная команда. Используйте /help")

def handle_file(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db.is_user_exists(user_id) and not db.is_admin(user_id):
        update.message.reply_text("⛔ Нет доступа")
        return

    if user_states.get(chat_id) != "waiting_text" and user_states.get(chat_id) != "waiting_task_type" and chat_id not in pending_categories:
        update.message.reply_text("❌ Сначала создайте задачу через /new")
        return

    document = update.message.document
    photo = update.message.photo

    if document:
        file_name = document.file_name or f"document_{int(time.time())}"
        mime_type = document.mime_type or "application/octet-stream"
        is_ok, msg = validate_file(document, file_name)
        if not is_ok:
            update.message.reply_text(msg)
            return
        tg_file = document.get_file()

    elif photo:
        file_name = f"photo_{int(time.time())}.jpg"
        mime_type = "image/jpeg"
        largest = photo[-1]
        if largest.file_size > config.MAX_FILE_SIZE:
            max_size_mb = config.MAX_FILE_SIZE / (1024 * 1024)
            update.message.reply_text(f"❌ Файл слишком большой. Максимальный размер: {max_size_mb:.0f} МБ")
            return
        tg_file = largest.get_file()
    else:
        return

    status_msg = update.message.reply_text(f"⏳ Сохраняю «{file_name}» на сервер...")

    try:
        file_bytes = tg_file.download_as_bytearray()
        temp_dir = os.path.join(config.FILES_STORAGE_PATH, "temp", str(chat_id))
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, file_name)

        with open(temp_file, 'wb') as f:
            f.write(file_bytes)

        if chat_id not in pending_files:
            pending_files[chat_id] = []
        pending_files[chat_id].append(file_name)

        if chat_id not in pending_file_links:
            pending_file_links[chat_id] = []
        pending_file_links[chat_id].append(temp_file)

        context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=f"✅ Файл сохранен: {file_name}\n"
                 f"📏 {len(file_bytes) / 1024:.1f} KB\n\n"
                 f"Отправьте еще файлы или текст задачи для завершения."
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения файла: {e}")
        context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=f"❌ Ошибка сохранения файла: {str(e)[:100]}"
        )

# ========== МОНИТОРИНГ РЕСУРСОВ ==========
def check_all_resources(notify=True):
    resources = db.get_resources()
    if not resources:
        return

    for res in resources:
        old_status = db.get_last_known_status(res["address"])

        if res["type"] == "http":
            result = check_http_direct(res["address"])
        elif res["type"] == "smtp":
            result = check_smtp_direct(res["address"], res["port"])
        else:
            continue

        current_status = result["status"]
        db.save_log(res["address"], current_status)

        if old_status == "UNKNOWN":
            db.set_last_known_status(res["address"], current_status)
            continue

        if old_status != current_status:
            db.set_last_known_status(res["address"], current_status)
            if current_status == "FAIL":
                send_log(f"🔴 {res['description']} упал! Ошибка: {result['details']}")
                notify_admins(res["address"], res["description"], res["type"], result, old_status)
            elif current_status == "OK" and old_status == "FAIL":
                send_log(f"🟢 {res['description']} восстановлен!")
                notify_admins_recovery(res["address"], res["description"], res["type"])

    update_pinned_status()

def check_http_direct(url):
    import time

    if not url.startswith("http"):
        url = "https://" + url

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                timeout=15,
                allow_redirects=True,
                headers=headers,
                proxies={'http': None, 'https': None}
            )
            is_ok = 200 <= response.status_code < 400
            return {
                "status": "OK" if is_ok else "FAIL",
                "details": f"HTTP {response.status_code}"
            }
        except requests.exceptions.Timeout:
            if attempt == 2:
                return {"status": "FAIL", "details": "Timeout (15s)"}
            time.sleep(2)
        except requests.exceptions.ConnectionError:
            if attempt == 2:
                return {"status": "FAIL", "details": "Connection error"}
            time.sleep(3)
        except Exception as e:
            if attempt == 2:
                return {"status": "FAIL", "details": str(e)[:100]}
            time.sleep(1)

    return {"status": "FAIL", "details": "Unknown error"}

def check_smtp_direct(host, port):
    import time

    for attempt in range(3):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((host, port or 25))
            sock.close()

            if result == 0:
                return {"status": "OK", "details": f"Порт {port or 25} доступен"}
            else:
                return {"status": "FAIL", "details": f"Порт {port or 25} недоступен (код: {result})"}
        except Exception as e:
            if attempt == 2:
                return {"status": "FAIL", "details": str(e)[:80]}
            time.sleep(1)

    return {"status": "FAIL", "details": "Unknown error"}

def notify_admins(resource, description, resource_type, result, old_status):
    admins = db.get_admin_list()
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    status_change = "🔴" if old_status == "OK" else "🟢"

    message = f"{status_change} *ИЗМЕНЕНИЕ СТАТУСА РЕСУРСА*\n\n"
    message += f"📝 *{description}*\n"
    message += f"📡 Адрес: {resource}\n"
    message += f"🔧 Тип: {resource_type.upper()}\n"
    message += f"📊 Статус: {old_status} → *{result['status']}*\n"
    message += f"⏰ Время: {now}\n"
    message += f"❌ Ошибка: {result['details']}"

    for admin in admins:
        try:
            updater.bot.send_message(chat_id=admin["id"], text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin['id']}: {e}")

def notify_admins_recovery(resource, description, resource_type):
    admins = db.get_admin_list()
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    message = f"🟢 *РЕСУРС ВОССТАНОВЛЕН*\n\n"
    message += f"📝 *{description}*\n"
    message += f"📡 Адрес: {resource}\n"
    message += f"🔧 Тип: {resource_type.upper()}\n"
    message += f"📊 Статус: FAIL → *OK*\n"
    message += f"⏰ Время: {now}"

    for admin in admins:
        try:
            updater.bot.send_message(chat_id=admin["id"], text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin['id']}: {e}")

# ========== АВТОМАТИЧЕСКАЯ ПРОВЕРКА ==========
def scheduled_check():
    logger.info(f"🔄 Автопроверка в {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    check_all_resources(notify=True)

def start_scheduler():
    def scheduler_loop():
        while True:
            try:
                scheduled_check()
            except Exception as e:
                logger.error(f"Ошибка автопроверки: {e}")
            time.sleep(900)

    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()
    logger.info("⏰ Автопроверка запущена (каждые 15 минут)")

# ========== ЗАПУСК БОТА ==========
def main():
    global updater

    print("🚀 Запуск бота...")
    print(f"📊 Таблица: {config.SHEET_ID}")
    print(f"🔒 Прокси (Telegram): {PROXY_URL}")
    print(f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    try:
        updater = Updater(
            token=config.BOT_TOKEN,
            use_context=True,
            request_kwargs={'proxy_url': PROXY_URL}
        )

        dp = updater.dispatcher

        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("help", help_command))
        dp.add_handler(CommandHandler("check", check_command))
        dp.add_handler(CommandHandler("status", status_command))
        dp.add_handler(CommandHandler("logs", logs_command))
        dp.add_handler(CommandHandler("updatepin", updatepin_command))

        dp.add_handler(CommandHandler("new", new_task))
        dp.add_handler(CommandHandler("cancel", cancel_command))
        dp.add_handler(CommandHandler("my", my_tasks))
        dp.add_handler(CommandHandler("status", task_status))
        dp.add_handler(CommandHandler("history", history_command))
        dp.add_handler(CommandHandler("files", files_command))

        dp.add_handler(CommandHandler("tasks", tasks_command))
        dp.add_handler(CommandHandler("taskstats", task_stats_command))
        dp.add_handler(CommandHandler("inprogress", inprogress_command))
        dp.add_handler(CommandHandler("resolve", resolve_command))
        dp.add_handler(CommandHandler("reject", reject_command))
        dp.add_handler(CommandHandler("comment", comment_command))
        dp.add_handler(CommandHandler("deletefiles", deletefiles_command))

        dp.add_handler(CallbackQueryHandler(approve_user_callback, pattern="^approve_"))
        dp.add_handler(CallbackQueryHandler(reject_user_callback, pattern="^reject_user_"))
        dp.add_handler(CallbackQueryHandler(button_callback, pattern="^(inprogress_|resolve_|reject_|files_|comment_|no_files|back_to_list|refresh_list|view_|toggle_completed)"))
        dp.add_handler(CallbackQueryHandler(problem_category_callback, pattern="^prob_"))
        dp.add_handler(CallbackQueryHandler(task_type_callback, pattern="^type_"))
        dp.add_handler(CallbackQueryHandler(cancel_command_callback, pattern="^cancel_all"))

        dp.add_handler(MessageHandler(Filters.document | Filters.photo, handle_file))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_comment_text))

        admins = db.get_admin_list()
        print(f"👑 Админов: {len(admins)}")
        for admin in admins:
            print(f" - {admin['name']} (ID: {admin['id']})")

        send_log("🤖 Бот поддержки запущен и готов к работе!")

        start_scheduler()

        print("🤖 Бот запущен!")
        print("⏳ Ожидание сообщений...")

        updater.start_polling()
        updater.idle()

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

def cancel_command_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id

    user_states.pop(chat_id, None)
    pending_categories.pop(chat_id, None)
    pending_problem_category.pop(chat_id, None)
    pending_files.pop(chat_id, None)
    pending_file_links.pop(chat_id, None)

    query.edit_message_text("❌ Создание задачи отменено")

if __name__ == "__main__":
    main()
