import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def get_service(service_account_file):
    try:
        creds = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        return build('sheets', 'v4', credentials=creds)
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def get_partners(service, spreadsheet_id):
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="A2:C1000"
    ).execute()
    rows = result.get('values', [])
    partners = []
    for row in rows:
        if len(row) >= 3 and row[2]:
            partners.append({"name": row[0] if row[0] else "Без имени", "chat_id": str(row[2])})
    logger.info(f"Загружено партнёров: {len(partners)}")
    return partners

def _next_free_row(service, spreadsheet_id):
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="A:A").execute()
    return len(result.get('values', [])) + 1

def append_rows(service, spreadsheet_id, rows):
    if not rows:
        return
    start_row = _next_free_row(service, spreadsheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"A{start_row}",
        valueInputOption='USER_ENTERED',
        body={'values': rows},
    ).execute()
    logger.info(f"В таблицу {spreadsheet_id} добавлено строк: {len(rows)}")

def read_all(service, spreadsheet_id, range_="A:Z"):
    try:
        result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_).execute()
        return result.get('values', [])
    except Exception as e:
        logger.error(f"Ошибка чтения таблицы {spreadsheet_id}: {e}")
        return []

def get_first_sheet_id(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return meta['sheets'][0]['properties']['sheetId']

def delete_rows_by_first_column(service, spreadsheet_id, values_to_delete):
    all_rows = read_all(service, spreadsheet_id)
    if not all_rows:
        return []
    sheet_id = get_first_sheet_id(service, spreadsheet_id)
    values_to_delete = set(str(v).strip() for v in values_to_delete)
    requests = []
    deleted = []
    for idx in range(len(all_rows) - 1, 0, -1):
        row = all_rows[idx]
        if row and str(row[0]).strip() in values_to_delete:
            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    }
                }
            })
            deleted.append(str(row[0]).strip())
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
        logger.info(f"Удалено строк: {len(deleted)}")
    return deleted
