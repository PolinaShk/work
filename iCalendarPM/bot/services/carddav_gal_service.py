import logging
import requests
import xml.etree.ElementTree as ET
import re
from bot.utils import decrypt_caldav_password

logger = logging.getLogger(__name__)

class CardDAVGALService:
    """Сервис для получения контактов из глобальной адресной книги (GAL) через CardDAV"""
    
    def __init__(self, username, encrypted_password):
        self.username = username
        self.password = decrypt_caldav_password(encrypted_password) if encrypted_password else encrypted_password
        self.base_url = "https://mail.id-east.ru/SOGo/dav"
        self._contacts_cache = None
        self.session = requests.Session()
        self.session.trust_env = False
        auth = self._get_auth()
        self.session.headers.update({
            "Authorization": f"Basic {auth}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/vcard"
        })
    
    def _get_auth(self):
        import base64
        return base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
    
    def get_all_contacts(self) -> list:
        """Получает все контакты из глобальной адресной книги через WebDAV"""
        try:
            if self._contacts_cache is not None:
                return self._contacts_cache
            
            # Пробуем разные возможные URL для GAL
            gal_urls = [
                f"{self.base_url}/GAL/",
                f"{self.base_url}/global/Contacts/",
                f"{self.base_url}/id-east.ru/Contacts/",
                f"{self.base_url}/{self.username}/Contacts/GAL/",
                f"{self.base_url}/Shared/Contacts/",
            ]
            
            all_contacts = []
            
            for gal_url in gal_urls:
                try:
                    logger.info(f"Пробуем GAL: {gal_url}")
                    contacts = self._fetch_contacts_from_url(gal_url)
                    if contacts:
                        all_contacts.extend(contacts)
                        logger.info(f"Найдено {len(contacts)} контактов в {gal_url}")
                        break
                except Exception as e:
                    logger.debug(f"Ошибка при обращении к {gal_url}: {e}")
                    continue
            
            self._contacts_cache = all_contacts
            logger.info(f"Всего загружено {len(all_contacts)} контактов из GAL")
            return all_contacts
            
        except Exception as e:
            logger.error(f"Ошибка получения GAL: {e}")
            return []
    
    def _fetch_contacts_from_url(self, url: str) -> list:
        """Загружает контакты из конкретного URL через PROPFIND"""
        propfind_body = '''<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:resourcetype/>
    <d:getetag/>
    <d:getcontenttype/>
    <d:displayname/>
  </d:prop>
</d:propfind>'''
        
        response = self.session.request(
            'PROPFIND',
            url,
            headers={'Depth': '1', 'Content-Type': 'application/xml'},
            data=propfind_body,
            timeout=30
        )
        
        if response.status_code != 207:
            return []
        
        root = ET.fromstring(response.text)
        ns = {'d': 'DAV:'}
        
        contacts = []
        for response_elem in root.findall('.//d:response', ns):
            href = response_elem.find('d:href', ns)
            if href is not None:
                href_text = href.text
                if '.vcf' in href_text:
                    full_url = f"https://mail.id-east.ru{href_text}" if href_text.startswith('/') else href_text
                    contact = self._fetch_vcard(full_url)
                    if contact:
                        contacts.append(contact)
        
        return contacts
    
    def _fetch_vcard(self, url: str) -> dict:
        """Загружает и парсит vCard файл"""
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return self._parse_vcard(response.text)
        except Exception as e:
            logger.error(f"Error fetching vCard {url}: {e}")
        return None
    
    def _parse_vcard(self, vcard_text: str) -> dict:
        """Парсит vCard и извлекает имя, фамилию, email"""
        contact = {
            'full_name': '',
            'first_name': '',
            'last_name': '',
            'emails': [],
            'phones': [],
            'title': '',
            'org': '',
            'uid': ''
        }
        
        lines = vcard_text.split('\n')
        for line in lines:
            line = line.strip()
            
            if line.startswith('FN:'):
                contact['full_name'] = line[3:].strip()
            
            elif line.startswith('N:'):
                parts = line[2:].split(';')
                if len(parts) >= 2:
                    contact['last_name'] = parts[0].strip()
                    contact['first_name'] = parts[1].strip()
            
            elif line.startswith('EMAIL:'):
                email = line[6:].strip()
                if email:
                    contact['emails'].append(email)
            
            elif line.startswith('TEL:'):
                phone = line[4:].strip()
                if phone:
                    contact['phones'].append(phone)
            
            elif line.startswith('TITLE:'):
                contact['title'] = line[6:].strip()
            
            elif line.startswith('ORG:'):
                contact['org'] = line[4:].strip()
            
            elif line.startswith('UID:'):
                contact['uid'] = line[4:].strip()
        
        # Если нет email, пробуем найти в других полях
        if not contact['emails']:
            email_match = re.search(r'EMAIL[^:]*:([^\n]+)', vcard_text, re.IGNORECASE)
            if email_match:
                contact['emails'].append(email_match.group(1).strip())
        
        if not contact['full_name'] and (contact['first_name'] or contact['last_name']):
            contact['full_name'] = f"{contact['first_name']} {contact['last_name']}".strip()
        
        return contact if contact['emails'] or contact['full_name'] else None
    
    def search_contacts(self, query: str, limit: int = 10) -> list:
        """Поиск контактов по имени или email"""
        contacts = self.get_all_contacts()
        if not contacts:
            return []
        
        query_lower = query.lower().strip()
        results = []
        
        for contact in contacts:
            name = contact.get('full_name', '').lower()
            first_name = contact.get('first_name', '').lower()
            last_name = contact.get('last_name', '').lower()
            emails = [e.lower() for e in contact.get('emails', [])]
            
            if (query_lower in name or 
                query_lower in first_name or 
                query_lower in last_name or
                any(query_lower in email for email in emails)):
                results.append(contact)
                if len(results) >= limit:
                    break
        
        return results
    
    def get_contact_by_email(self, email: str) -> dict:
        """Получить контакт по точному email"""
        contacts = self.get_all_contacts()
        email_lower = email.lower().strip()
        
        for contact in contacts:
            for e in contact.get('emails', []):
                if e.lower() == email_lower:
                    return contact
        return None
    
    def clear_cache(self):
        self._contacts_cache = None


def search_contacts_by_name(username, encrypted_password, query, limit=10):
    service = CardDAVGALService(username, encrypted_password)
    return service.search_contacts(query, limit)


def get_contact_by_email(username, encrypted_password, email):
    service = CardDAVGALService(username, encrypted_password)
    return service.get_contact_by_email(email)
