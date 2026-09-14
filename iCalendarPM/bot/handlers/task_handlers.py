from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from bot.services.task_manager import TaskManager
from bot.logger import send_log, send_error, send_success, send_info
import logging

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
TASK_ID, TASK_TITLE, TASK_DESCRIPTION, TASK_STATUS, TASK_COMMENT, TASK_HISTORY = range(6)

async def task_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало создания задачи"""
    await update.message.reply_text(
        "📋 <b>Создание новой задачи</b>\n\n"
        "Введите ID задачи (например: TASK-001):\n"
        "(для отмены отправьте /cancel)",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return TASK_ID

async def task_create_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ID задачи"""
    context.user_data["task_id"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 Введите название задачи:",
        disable_web_page_preview=True
    )
    return TASK_TITLE

async def task_create_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия задачи"""
    context.user_data["task_title"] = update.message.text.strip()
    await update.message.reply_text(
        "📄 Введите описание задачи (или '-' чтобы пропустить):",
        disable_web_page_preview=True
    )
    return TASK_DESCRIPTION

async def task_create_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание задачи"""
    description = update.message.text.strip()
    if description == "-":
        description = ""
    
    task_id = context.user_data.get("task_id")
    title = context.user_data.get("task_title")
    user_id = update.effective_user.id
    
    result = await TaskManager.create_task(task_id, title, description, user_id)
    
    if result["success"]:
        await update.message.reply_text(
            f"✅ Задача <b>{task_id}</b> создана!\n\n"
            f"📌 Название: {title}\n"
            f"📄 Описание: {description or 'нет'}\n"
            f"📊 Статус: новая",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            f"❌ {result['error']}",
            disable_web_page_preview=True
        )
    
    return ConversationHandler.END

async def task_status_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало изменения статуса"""
    await update.message.reply_text(
        "📊 <b>Изменение статуса задачи</b>\n\n"
        "Введите ID задачи:",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return TASK_STATUS

async def task_status_get_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ID задачи для смены статуса"""
    context.user_data["task_id"] = update.message.text.strip()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Новая", callback_data="status_новая")],
        [InlineKeyboardButton("🟡 В работе", callback_data="status_в работе")],
        [InlineKeyboardButton("🔵 На проверке", callback_data="status_на проверке")],
        [InlineKeyboardButton("🟢 Готово", callback_data="status_готово")],
        [InlineKeyboardButton("🔴 Отменена", callback_data="status_отменена")],
    ])
    
    await update.message.reply_text(
        "Выберите новый статус:",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )
    return TASK_STATUS

async def task_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора статуса"""
    query = update.callback_query
    await query.answer()
    
    status = query.data.replace("status_", "")
    task_id = context.user_data.get("task_id")
    user_id = update.effective_user.id
    
    result = await TaskManager.update_task_status(task_id, status, user_id)
    
    if result["success"]:
        await query.edit_message_text(
            f"✅ Статус задачи <b>{task_id}</b> изменён!\n\n"
            f"📊 {result['old_status']} → {result['new_status']}",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        await query.edit_message_text(
            f"❌ {result['error']}",
            disable_web_page_preview=True
        )
    
    return ConversationHandler.END

async def task_history_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр истории задачи"""
    await update.message.reply_text(
        "📜 <b>История изменений задачи</b>\n\n"
        "Введите ID задачи:",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return TASK_HISTORY

async def task_history_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ истории задачи"""
    task_id = update.message.text.strip()
    
    history = await TaskManager.get_task_history(task_id)
    comments = await TaskManager.get_task_comments(task_id)
    
    if not history and not comments:
        await update.message.reply_text(
            f"❌ Задача <b>{task_id}</b> не найдена или нет истории",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return ConversationHandler.END
    
    msg = f"📜 <b>История задачи {task_id}</b>\n\n"
    
    if history:
        msg += "📋 <b>Изменения:</b>\n"
        for h in history[:10]:
            msg += f"• {h['changed_at']} — {h['changed_by_name']}\n"
            msg += f"  {h['field_name']}: {h['old_value']} → {h['new_value']}\n"
            if h['comment']:
                msg += f"  💬 {h['comment']}\n"
        msg += "\n"
    
    if comments:
        msg += "💬 <b>Комментарии:</b>\n"
        for c in comments[:10]:
            msg += f"• {c['created_at']} — {c['author_name']}: {c['comment']}\n"
    
    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
    return ConversationHandler.END

async def task_comment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление комментария к задаче"""
    await update.message.reply_text(
        "💬 <b>Добавление комментария</b>\n\n"
        "Введите ID задачи:",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return TASK_COMMENT

async def task_comment_get_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ID задачи для комментария"""
    context.user_data["task_id"] = update.message.text.strip()
    await update.message.reply_text(
        "✏️ Введите комментарий:",
        disable_web_page_preview=True
    )
    return TASK_COMMENT

async def task_comment_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение комментария"""
    comment = update.message.text.strip()
    task_id = context.user_data.get("task_id")
    user_id = update.effective_user.id
    
    result = await TaskManager.add_comment(task_id, user_id, comment)
    
    if result["success"]:
        await update.message.reply_text(
            f"✅ Комментарий добавлен к задаче <b>{task_id}</b>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            f"❌ {result['error']}",
            disable_web_page_preview=True
        )
    
    return ConversationHandler.END

async def task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всех задач"""
    tasks = await TaskManager.get_all_tasks(limit=20)
    
    if not tasks:
        await update.message.reply_text("📋 Нет созданных задач.", disable_web_page_preview=True)
        return
    
    msg = "📋 <b>Список задач:</b>\n\n"
    for t in tasks:
        status_emoji = {
            "новая": "🟢",
            "в работе": "🟡",
            "на проверке": "🔵",
            "готово": "✅",
            "отменена": "❌"
        }.get(t["status"], "⚪")
        
        msg += f"{status_emoji} <b>{t['task_id']}</b> — {t['title']}\n"
        msg += f"   📊 {t['status']} | 👤 {t['created_by_name']}\n"
        msg += f"   📅 {t['created_at']}\n\n"
    
    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

async def task_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена"""
    context.user_data.clear()
    await update.message.reply_text("❌ Операция отменена.", disable_web_page_preview=True)
    return ConversationHandler.END
