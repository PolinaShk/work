import requests
from datetime import datetime
import pytz
import config

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# Используем токен из config
LOG_BOT_TOKEN = config.TOKEN

# ПРАВИЛЬНЫЙ ID чата (из вашей ссылки)
LOG_CHAT_ID = -1004398037562  # ← этот ID

# Прокси из config
TELEGRAM_PROXIES = None
if config.USE_PROXY_FOR_TELEGRAM and config.TELEGRAM_PROXY:
    TELEGRAM_PROXIES = {
        'http': config.TELEGRAM_PROXY,
        'https': config.TELEGRAM_PROXY,
    }

def send_log(bot_name, user, action, event_type="info"):
    moscow_time = datetime.now(MOSCOW_TZ)
    formatted_time = moscow_time.strftime("%d.%m.%Y %H:%M:%S")
    
    emojis = {"info": "ℹ️", "success": "✅", "error": "❌", "warning": "⚠️"}
    emoji = emojis.get(event_type, "📌")
    
    text = (
        f"{emoji} <b>{bot_name}</b>\n"
        f"👤 <b>Пользователь:</b> {user}\n"
        f"📋 <b>Действие:</b> {action}\n"
        f"🕐 <b>Время:</b> {formatted_time}"
    )
    
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": LOG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "message_thread_id": 9,  # ← ID темы "Топливник бот"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10, proxies=TELEGRAM_PROXIES)
        if response.status_code == 200:
            print(f"✅ Лог отправлен в тему 9")
            return True
        else:
            print(f"⚠️ Ошибка: {response.status_code} - {response.text[:100]}")
            return False
    except Exception as e:
        print(f"⚠️ Ошибка отправки лога: {e}")
        return False

def send_error(bot_name, user, action):
    return send_log(bot_name, user, action, "error")

def send_success(bot_name, user, action):
    return send_log(bot_name, user, action, "success")

def send_info(bot_name, user, action):
    return send_log(bot_name, user, action, "info")