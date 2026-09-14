import logging
from bot.services.google_sheets_service import GoogleContactsSearch

logger = logging.getLogger(__name__)

class CardDAVService:
    def __init__(self, username, encrypted_password):
        self.username = username
        self.password = encrypted_password
        self._service = GoogleContactsSearch(username, encrypted_password)
    
    def search_contacts(self, query: str, limit: int = 10) -> list:
        return self._service.search_contacts(query, limit)
    
    def get_contact_by_email(self, email: str) -> dict:
        return self._service.get_contact_by_email(email)


def search_contacts_by_name(username, encrypted_password, query, limit=10):
    service = CardDAVService(username, encrypted_password)
    return service.search_contacts(query, limit)


def get_contact_by_email(username, encrypted_password, email):
    service = CardDAVService(username, encrypted_password)
    return service.get_contact_by_email(email)
