from bot.logger import send_info, send_error, send_success
import logging

logger = logging.getLogger(__name__)
BOT_NAME = "Календарь бот"

async def send_log(message: str, log_type: str = "info"):
    """Отправляет лог в тему 10 (Календарь бот)"""
    try:
        if log_type == "error":
            await send_error(BOT_NAME, "Система", message)
        elif log_type == "success":
            await send_success(BOT_NAME, "Система", message)
        else:
            await send_info(BOT_NAME, "Система", message)
    except Exception as e:
        logger.error(f"Ошибка отправки лога: {e}")
