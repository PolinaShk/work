"""
Проверка почты SOGo через IMAP с извлечением правильной ссылки.
"""
import imaplib
import email
import re
import logging
import ssl
from email.header import decode_header

import config

logger_email = logging.getLogger(__name__)

SUBJECT_MARKER = "приглашает принять участие в процедуре"

# === РЕГУЛЯРКИ ===
MB_NUMBER_RE = re.compile(r"(МБ|НРД|НКЦ)-СПЦ-\d{4}-(\d+)")
MB_NUMBER_SHORT_RE = re.compile(r"(МБ|НРД|НКЦ)-(\d+)", re.IGNORECASE)
MB_NUMBER_LATIN_RE = re.compile(r"(MB|NRD|NKC)-(\d+)", re.IGNORECASE)
DEADLINE_RE = re.compile(r"Подача\s+заявки\s+до:\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", re.IGNORECASE)
LINK_HTML_RE = re.compile(r'<a[^>]+href=["\'](https?://[^"\']+b2b-center\.ru/[^"\']+)["\'][^>]*>Перейти\s+к\s+процедуре', re.IGNORECASE | re.DOTALL)
LINK_URL_RE = re.compile(r'https?://[^\s<>"\']+b2b-center\.ru[^\s<>"\']*', re.IGNORECASE)


def _decode_header_value(value):
    if not value:
        return ""
    parts = decode_header(value)
    result = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                result += text.decode("utf-8", errors="ignore")
            except:
                result += text.decode("windows-1251", errors="ignore")
        else:
            result += text
    return result


def _get_body_text(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                for charset in ["utf-8", "windows-1251", "cp1251", "koi8-r"]:
                    try:
                        body += payload.decode(charset)
                        break
                    except:
                        continue
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                for charset in ["utf-8", "windows-1251", "cp1251", "koi8-r"]:
                    try:
                        body = payload.decode(charset)
                        break
                    except:
                        continue
        except Exception:
            pass
    return body


def _extract_link_from_html(html):
    m = LINK_HTML_RE.search(html)
    if m:
        return m.group(1)
    matches = LINK_URL_RE.findall(html)
    for link in matches:
        if 'view.html' in link and 'action=positions' in link:
            return link
        if 'tender' in link:
            return link
        if 'logo' not in link and 'image' not in link and 'png' not in link:
            return link
    return None


def _extract_mb_number(text):
    m = MB_NUMBER_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = MB_NUMBER_SHORT_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = MB_NUMBER_LATIN_RE.search(text)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return None


def _extract_deadline(text):
    m = DEADLINE_RE.search(text)
    if not m:
        date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
        if date_match:
            day, month, year = date_match.groups()
            time_match = re.search(r'(\d{2}):(\d{2})', text)
            if time_match:
                hh, mm = time_match.groups()
                hour = int(hh) - 2
                if hour < 0:
                    hour += 24
                time_str = f"{hour:02d}:{mm}"
            else:
                time_str = None
            return f"{day}-{month}-{year[2:]}", time_str
        return None, None
    day, month, year, hh, mm = m.groups()
    date_str = f"{day}-{month}-{year[2:]}"
    hour = int(hh) - 2
    if hour < 0:
        hour += 24
    time_str = f"{hour:02d}:{mm}"
    return date_str, time_str


async def fetch_new_procedures(host=None, address=None, app_password=None, mailbox="INBOX", mark_seen=True):
    host = host or config.SOGO_HOST
    address = address or config.SOGO_EMAIL
    app_password = app_password or config.SOGO_PASSWORD

    procedures = []
    
    try:
        context = ssl._create_unverified_context()
        imap = imaplib.IMAP4_SSL(host, config.SOGO_PORT, ssl_context=context)
        logger_email.info(f"✅ Подключение к SOGo {host} успешно")
    except Exception as e:
        logger_email.error(f"❌ Не удалось подключиться к SOGo: {e}")
        return procedures

    try:
        imap.login(address, app_password)
        logger_email.info("✅ Авторизация в SOGo успешна")
        imap.select(mailbox)
        
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            logger_email.warning(f"IMAP SEARCH не удался: {status}")
            return procedures
        
        uids = data[0].split()
        logger_email.info(f"📨 Непрочитанных писем: {len(uids)}")
        
        for uid in uids:
            status, msg_data = imap.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = _decode_header_value(msg.get("Subject"))
            
            if SUBJECT_MARKER not in subject:
                continue
            
            body = _get_body_text(msg)
            if not body:
                continue
            
            link = _extract_link_from_html(body)
            mb_number = _extract_mb_number(body)
            date_str, time_str = _extract_deadline(body)
            
            if not (link and mb_number and date_str):
                logger_email.warning(
                    "⚠️ Не удалось разобрать письмо '%s': link=%s, mb=%s, date=%s",
                    subject, link, mb_number, date_str,
                )
                if mark_seen:
                    imap.store(uid, '+FLAGS', '\\Seen')
                procedures.append({
                    "mb_number": mb_number or "Неизвестно",
                    "date": date_str or "Неизвестно",
                    "time": time_str or "",
                    "link": link or "",
                    "subject": subject,
                    "parse_error": True,
                })
                continue
            
            procedures.append({
                "mb_number": mb_number,
                "date": date_str,
                "time": time_str or "",
                "link": link,
                "subject": subject,
                "parse_error": False,
            })
            
            if mark_seen:
                imap.store(uid, '+FLAGS', '\\Seen')
                logger_email.info(f"✅ Письмо {subject} обработано")
                
    except Exception as e:
        logger_email.error(f"❌ Ошибка при работе с SOGo: {e}")
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass
    
    return procedures
