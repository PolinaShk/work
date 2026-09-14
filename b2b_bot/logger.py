import logging
from datetime import datetime, timedelta, timezone
import config

logger = logging.getLogger(__name__)
_bot = None

# Фиксируем московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

def set_bot(bot):
    global _bot
    _bot = bot

async def log_action(action, event_type="info", details=None):
    if not _bot:
        return

    emojis = {"info": "ℹ️", "success": "✅", "error": "❌", "warning": "⚠️", "start": "▶️", "mail": "📧", "b2b": "🌐", "sheets": "📊", "telegram": "🤖"}
    emoji = emojis.get(event_type, "📌")
    
    # Используем МОСКОВСКОЕ время
    now = datetime.now(MOSCOW_TZ)
    formatted_time = now.strftime("%d.%m.%Y %H:%M:%S")

    message = f"{emoji} <b>{config.BOT_NAME}</b>\n📋 <b>Действие:</b> {action}\n🕐 <b>Время:</b> {formatted_time}"
    if details:
        if isinstance(details, dict):
            for key, value in details.items():
                if value is not None:
                    message += f"\n📌 <b>{key}:</b> {str(value)[:500]}"
        else:
            message += f"\n📌 {details}"

    try:
        await _bot.send_message(chat_id=config.LOG_CHAT_ID, text=message, parse_mode="HTML", message_thread_id=config.LOG_TOPIC_ID, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка отправки лога: {e}")
