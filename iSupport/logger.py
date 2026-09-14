# -*- coding: utf-8 -*-
import requests
from datetime import datetime
import json
import os
from dotenv import load_dotenv
import pytz

load_dotenv()

LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN") or os.getenv("BOT_TOKEN")
LOG_CHAT_ID = os.getenv("LOGS_CHAT_ID") or os.getenv("STATUS_CHAT_ID")
LOG_TOPIC_ID = os.getenv("LOGS_TOPIC_ID")

PROXY_URL = os.getenv("PROXY_URL", "socks5://192.168.2.100:8085")
USE_PROXY = os.getenv("USE_PROXY", "true").lower() == "true"

if USE_PROXY and PROXY_URL:
    TELEGRAM_PROXIES = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }
    print(f"📡 logger использует прокси: {PROXY_URL}")
else:
    TELEGRAM_PROXIES = None
    print("📡 logger работает без прокси")

LOG_FILE = os.getenv("LOG_FILE", "/app/logs/bot.log")

EMOJIS = {
    "info": "ℹ️",
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "critical": "🚨",
    "start": "▶️",
}

_warned_no_creds = False
MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def _write_to_file(entry):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Не удалось записать лог в файл: {e}")


def send_log(bot_name, user, action, event_type="info"):
    global _warned_no_creds

    moscow_time = datetime.now(MOSCOW_TZ)
    formatted_time = moscow_time.strftime("%d.%m.%Y %H:%M:%S")

    entry = {
        "bot_name": bot_name,
        "user": user,
        "action": action,
        "event_type": event_type,
        "timestamp": moscow_time.isoformat(),
    }

    if not LOG_BOT_TOKEN or not LOG_CHAT_ID or not LOG_TOPIC_ID:
        if not _warned_no_creds:
            print(f"⚠️ LOG_BOT_TOKEN/LOG_CHAT_ID/LOG_TOPIC_ID не заданы")
            _warned_no_creds = True
        _write_to_file(entry)
        return False

    emoji = EMOJIS.get(event_type, "📌")

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
        "message_thread_id": int(LOG_TOPIC_ID),
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10,
            proxies=TELEGRAM_PROXIES
        )
        if response.status_code == 200 and response.json().get("ok"):
            print(f"✅ Лог отправлен в тему {LOG_TOPIC_ID}")
            return True
        print(f"⚠️ Ошибка: {response.status_code} {response.text[:200]}")
    except Exception as e:
        print(f"⚠️ Не удалось отправить лог: {e}")

    _write_to_file(entry)
    return False


def send_error(bot_name, user, action):
    return send_log(bot_name, user, action, "error")


def send_success(bot_name, user, action):
    return send_log(bot_name, user, action, "success")


def send_info(bot_name, user, action):
    return send_log(bot_name, user, action, "info")
