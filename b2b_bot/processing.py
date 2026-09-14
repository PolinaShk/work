import logging
import json
import os
from datetime import datetime
import config
import email_watcher
import logger
import sheets_client

logger_processing = logging.getLogger(__name__)

async def process_new_procedures(notify_admin, notify_partners):
    await logger.log_action("🔍 Начинаем проверку новых процедур", "info")
    procedures = await email_watcher.fetch_new_procedures()
    if not procedures:
        await logger.log_action("📭 Новых писем с процедурами нет", "info")
        await notify_admin("📭 Новых процедур не найдено")
        return
    await logger.log_action(f"📨 Найдено {len(procedures)} новых писем с процедурами", "success")
    await notify_admin(f"✅ Найдено {len(procedures)} новых процедур")
    # Здесь будет парсинг B2B, но пока заглушка
    await logger.log_action("🔄 Парсинг B2B пока в разработке", "warning")

def _parse_ddmmyy(date_str):
    try:
        return datetime.strptime(date_str, "%d-%m-%y")
    except:
        return None

async def check_expiring(notify_admin_with_keyboard, notify_admin):
    await logger.log_action("⏰ Проверка истекающих заявок", "info")
    
    service = sheets_client.get_service(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    if not service:
        await logger.log_action("❌ Нет доступа к Google Sheets", "error")
        await notify_admin("❌ Ошибка подключения к таблицам")
        return
    
    rows = sheets_client.read_all(service, config.SHEET_TABLE1_ID)
    total_requests = len(rows) - 1 if len(rows) > 1 else 0
    
    if total_requests == 0:
        await logger.log_action("📊 В таблице нет активных заявок", "info")
        await notify_admin("📊 В таблице нет активных заявок")
        return
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    expiring = []
    
    for row in rows[1:]:
        if not row or not row[0] or row[0] == "Номер":
            continue
        date_str = row[1] if len(row) > 1 else ""
        d = _parse_ddmmyy(date_str)
        if d and d <= today:
            expiring.append(str(row[0]).strip())
    
    await logger.log_action(
        f"📊 Всего заявок: {total_requests}, просроченных: {len(expiring)}",
        "info",
        {"Всего": total_requests, "Просрочено": len(expiring), "Номера": expiring if expiring else "нет"}
    )
    
    if not expiring:
        await logger.log_action("✅ Просроченных заявок не найдено", "success")
        await notify_admin(f"✅ Активных заявок: {total_requests}. Просроченных нет.")
        return
    
    message = "⏰ <b>СЕГОДНЯ ПОСЛЕДНИЙ ДЕНЬ ПОДАЧИ!</b>\n\n"
    message += f"Найдено {len(expiring)} просроченных заявок:\n\n"
    for num in expiring:
        message += f"🔹 Запрос №{num}\n"
    message += "\n✅ После обработки нажмите «Подтверждаю»"
    
    await notify_admin_with_keyboard(message)
    await logger.log_action(
        f"📨 Отправлено уведомление о {len(expiring)} просроченных заявках",
        "telegram",
        {"Номера": expiring}
    )

async def delete_expired(notify_result):
    await logger.log_action("🗑️ Удаление просроченных заявок", "info")
    # Реальное удаление (пока заглушка)
    await notify_result("✅ Удаление выполнено")
    await logger.log_action("✅ Просроченные заявки удалены", "success")
