import httpx
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
LOGS_CHAT_ID = os.getenv("LOGS_CHAT_ID")
PROXY_URL = os.getenv("PROXY_URL")
TG_API_URL = os.getenv("TG_API_URL", "https://api.telegram.org")

TOPIC_MAPPING = {
    "Календарь бот": 10,
}

BOT_NAME = "Календарь бот"

EMOJIS = {
    "info": "ℹ️",
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "critical": "🚨",
    "start": "▶️",
}


async def send_log(bot_name: str, user: str, action: str, event_type: str = "info"):
    if not LOGS_CHAT_ID:
        return

    topic_id = TOPIC_MAPPING.get(bot_name)
    if not topic_id:
        return

    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    emoji = EMOJIS.get(event_type, "📌")

    message = (
        f"{emoji} <b>{bot_name}</b>\n"
        f"👤 <b>Пользователь:</b> {user}\n"
        f"📋 <b>Действие:</b> {action}\n"
        f"🕐 <b>Время:</b> {timestamp}"
    )

    if len(message) > 4000:
        message = message[:3997] + "..."

    # Формируем URL правильно
    base_url = TG_API_URL.rstrip('/')
    # Если в URL уже есть /bot, убираем его, чтобы не было дублирования
    if base_url.endswith('/bot'):
        base_url = base_url[:-4]
    url = f"{base_url}/bot{BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": LOGS_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "message_thread_id": topic_id,
    }

    try:
        if PROXY_URL:
            async with httpx.AsyncClient(proxy=PROXY_URL) as client:
                await client.post(url, json=payload, timeout=10)
        else:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка отправки лога: {e}")


async def send_error(bot_name: str, user: str, action: str):
    await send_log(bot_name, user, action, "error")


async def send_success(bot_name: str, user: str, action: str):
    await send_log(bot_name, user, action, "success")


async def send_info(bot_name: str, user: str, action: str):
    await send_log(bot_name, user, action, "info")
