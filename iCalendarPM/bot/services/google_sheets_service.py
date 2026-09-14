import os
import json
import logging
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

class GoogleSheetsService:
    """Сервис для работы с Google Sheets"""
    
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    
    SHEET1_NAME = 'id-east.ru'
    SHEET2_NAME = 'softglobal.ru'
    
    def __init__(self, credentials_json: str = None, sheet_id: str = None):
        self.credentials_json = credentials_json or os.getenv('GOOGLE_CREDENTIALS_PATH', '/app/google_credentials.json')
        self.sheet_id = sheet_id or os.getenv('GOOGLE_SHEET_ID', '1R52uhwR5HnXyrE22Clp0hJuqI4blCuvOxVch797_t3U')
        self._service = None
        self._cache = {}
        
    def _get_service(self):
        if self._service is None:
            if not self.credentials_json or not os.path.exists(self.credentials_json):
                raise FileNotFoundError(f"Файл с ключами Google не найден: {self.credentials_json}")
            
            with open(self.credentials_json, 'r') as f:
                creds_data = json.load(f)
            
            creds = Credentials.from_service_account_info(creds_data, scopes=self.SCOPES)
            self._service = build('sheets', 'v4', credentials=creds)
        return self._service
    
    def search_contacts(self, query: str, limit: int = 10, search_all_sheets: bool = False) -> List[Dict]:
        """Поиск контактов по email, имени или фамилии"""
        try:
            all_results = []
            
            data1 = self._get_sheet1_data()
            if data1:
                results1 = self._search_in_data(data1, query, limit)
                all_results.extend(results1)
            
            if search_all_sheets:
                data2 = self._get_sheet2_data()
                if data2:
                    results2 = self._search_in_data(data2, query, limit - len(all_results))
                    all_results.extend(results2)
            
            return all_results[:limit]
            
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            return []
    
    def _get_sheet1_data(self) -> List[Dict]:
        sheet_name = self.SHEET1_NAME
        if sheet_name in self._cache and self._cache[sheet_name] is not None:
            return self._cache[sheet_name]
        
        try:
            service = self._get_service()
            
            result = service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=f"'{sheet_name}'!A:P"
            ).execute()
            
            values = result.get('values', [])
            if not values or len(values) < 2:
                logger.warning(f"Лист {sheet_name} пуст")
                return []
            
            result_data = []
            for row in values[1:]:
                entry = {
                    'email': row[0] if len(row) > 0 else '',
                    'first_name': row[1] if len(row) > 1 else '',
                    'last_name': row[2] if len(row) > 2 else '',
                    'groups': row[5] if len(row) > 5 else '',
                    'status': row[15] if len(row) > 15 else '',
                    'sheet': sheet_name
                }
                
                if entry.get('email') or entry.get('first_name') or entry.get('last_name'):
                    result_data.append(entry)
            
            self._cache[sheet_name] = result_data
            logger.info(f"Загружено {len(result_data)} записей из справочника {sheet_name}")
            return result_data
            
        except Exception as e:
            logger.error(f"Ошибка получения данных из справочника {sheet_name}: {e}")
            return []
    
    def _get_sheet2_data(self) -> List[Dict]:
        sheet_name = self.SHEET2_NAME
        if sheet_name in self._cache and self._cache[sheet_name] is not None:
            return self._cache[sheet_name]
        
        try:
            service = self._get_service()
            
            result = service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=f"'{sheet_name}'!A:P"
            ).execute()
            
            values = result.get('values', [])
            if not values or len(values) < 2:
                logger.warning(f"Лист {sheet_name} пуст")
                return []
            
            result_data = []
            for row in values[1:]:
                entry = {
                    'email': row[0] if len(row) > 0 else '',
                    'first_name': row[1] if len(row) > 1 else '',
                    'last_name': row[2] if len(row) > 2 else '',
                    'groups': row[6] if len(row) > 6 else '',
                    'status': row[14] if len(row) > 14 else '',
                    'sheet': sheet_name
                }
                
                if entry.get('email') or entry.get('first_name') or entry.get('last_name'):
                    result_data.append(entry)
            
            self._cache[sheet_name] = result_data
            logger.info(f"Загружено {len(result_data)} записей из справочника {sheet_name}")
            return result_data
            
        except Exception as e:
            logger.error(f"Ошибка получения данных из справочника {sheet_name}: {e}")
            return []
    
    def _search_in_data(self, data: List[Dict], query: str, limit: int) -> List[Dict]:
        if not data:
            return []
        
        results = []
        query_lower = query.lower().strip()
        
        for row in data:
            status = row.get('status', '').lower()
            if status not in ['готово', 'готов', 'да', 'done']:
                continue
            
            email = row.get('email', '').lower()
            first_name = row.get('first_name', '').lower()
            last_name = row.get('last_name', '').lower()
            full_name = f"{first_name} {last_name}".strip()
            
            if (query_lower in email or 
                query_lower in first_name or 
                query_lower in last_name or
                query_lower in full_name):
                
                results.append({
                    'email': row.get('email', ''),
                    'first_name': row.get('first_name', ''),
                    'last_name': row.get('last_name', ''),
                    'display_name': f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                    'status': row.get('status', ''),
                    'groups': row.get('groups', ''),
                    'sheet': row.get('sheet', '')
                })
                
                if len(results) >= limit:
                    break
        
        return results
    
    def clear_cache(self):
        self._cache = {}


class GoogleContactsSearch:
    def __init__(self, username, encrypted_password):
        self.service = GoogleSheetsService()
    
    def search_contacts(self, query: str, limit: int = 10) -> List[Dict]:
        return self.service.search_contacts(query, limit, search_all_sheets=False)
    
    def search_contacts_all_sheets(self, query: str, limit: int = 10) -> List[Dict]:
        return self.service.search_contacts(query, limit, search_all_sheets=True)
    
    def get_contact_by_email(self, email: str) -> Optional[Dict]:
        contacts = self.service.search_contacts(email, limit=1, search_all_sheets=False)
        for contact in contacts:
            if contact.get('email', '').lower() == email.lower():
                return contact
        return None


def search_contacts_by_name(username, encrypted_password, query, limit=10):
    service = GoogleContactsSearch(username, encrypted_password)
    return service.search_contacts(query, limit)


def search_contacts_all_sheets(username, encrypted_password, query, limit=10):
    service = GoogleContactsSearch(username, encrypted_password)
    return service.search_contacts_all_sheets(query, limit)


def get_contact_by_email(username, encrypted_password, email):
    service = GoogleContactsSearch(username, encrypted_password)
    return service.get_contact_by_email(email)
