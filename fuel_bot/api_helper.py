# -*- coding: utf-8 -*-
import requests
import json
from datetime import datetime, timedelta
import time
import socket
import config
import logging
import os

# Отключаем предупреждения о SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

url = 'https://api.opti-24.ru'
api_key = 'GPN.02231083b9400e0078b24d65677fe0b8fcb85193.08902e4def7b9a012f47ce1cd6a51a088564945c'

AUTH_DATA = {
    'login': 'ideast',
    'password': '8d9ce1e59bd5e3c4a4532200dcb41027c8718aa35e0ef0bbc180d3cb60a259f36da7a8856a366fbfb5f0ef6ea9e67c07adb1a7778db9682c7e768e9b813fb310'
}

# Кэш сессии
_session_cache = {
    'session_id': None,
    'contract_id': None,
    'timestamp': 0
}
CACHE_TTL = 300

# Сохраняем оригинальные переменные окружения
_saved_proxy_env = {}


def _disable_proxy():
    """Временно отключаем все прокси для API запросов"""
    global _saved_proxy_env
    _saved_proxy_env = {}
    proxy_keys = ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'SOCKS5_PROXY', 
                  'http_proxy', 'https_proxy', 'all_proxy', 'socks5_proxy']
    for key in proxy_keys:
        if key in os.environ:
            _saved_proxy_env[key] = os.environ.pop(key)
    if _saved_proxy_env:
        logger.debug(f"🔧 Отключены прокси: {list(_saved_proxy_env.keys())}")


def _enable_proxy():
    """Восстанавливаем прокси"""
    if _saved_proxy_env:
        for key, value in _saved_proxy_env.items():
            os.environ[key] = value
        logger.debug("🔧 Восстановлены прокси")


def authenticate(max_retries=3):
    """Авторизация с повторными попытками (БЕЗ прокси)"""
    current_time = time.time()
    
    # Проверяем кэш
    if _session_cache['session_id'] and _session_cache['contract_id']:
        if current_time - _session_cache['timestamp'] < CACHE_TTL:
            logger.info("🔄 Использование кэшированной сессии")
            return _session_cache['session_id'], _session_cache['contract_id']
    
    # Отключаем прокси для API запросов
    _disable_proxy()
    
    try:
        for attempt in range(max_retries):
            try:
                logger.info(f"🔑 Авторизация в API Opti... (попытка {attempt+1}/{max_retries})")
                
                r = requests.post(
                    f'{url}/vip/v1/authUser',
                    json=AUTH_DATA,
                    headers={'Content-Type': 'application/json', 'api_key': api_key},
                    timeout=getattr(config, 'API_TIMEOUT', 60),
                    verify=False
                )
                
                logger.info(f"📥 Статус ответа: {r.status_code}")
                
                if r.status_code != 200:
                    logger.error(f"⚠️ Ошибка авторизации: статус {r.status_code}")
                    logger.error(f"Ответ: {r.text[:200]}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, None
                    
                data = r.json()
                
                # Проверяем статус
                status = data.get('status', {})
                if status.get('code') != 200:
                    error_msg = data.get('message', 'неизвестная ошибка')
                    logger.error(f"⚠️ Ошибка авторизации: {error_msg}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, None
                    
                response_data = data.get('data', {})
                session_id = response_data.get('session_id')
                contracts = response_data.get('contracts', [])
                
                logger.info(f"🔑 Получен session_id: {session_id[:50]}...")
                logger.info(f"📋 Получено контрактов: {len(contracts)}")
                
                contract_id = None
                for contract in contracts:
                    cards_count = contract.get('cards_count', 0)
                    logger.info(f"  Контракт: {contract.get('number')}, карт: {cards_count}")
                    if cards_count > 0:
                        contract_id = contract.get('id')
                        logger.info(f"✅ Выбран контракт с картами: {contract.get('number')}")
                        break
                        
                if not contract_id and contracts:
                    contract_id = contracts[0].get('id')
                    logger.info(f"✅ Используем первый контракт: {contracts[0].get('number')}")
                    
                if session_id and contract_id:
                    _session_cache['session_id'] = session_id
                    _session_cache['contract_id'] = contract_id
                    _session_cache['timestamp'] = current_time
                    logger.info("✅ Авторизация успешна!")
                    return session_id, contract_id
                else:
                    logger.error(f"❌ Не удалось получить данные: session_id={session_id is not None}, contract_id={contract_id}")
                    
            except requests.exceptions.Timeout:
                logger.error(f"⚠️ Таймаут авторизации, попытка {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            except Exception as e:
                logger.error(f"⚠️ Ошибка авторизации: {e}, попытка {attempt+1}/{max_retries}")
                import traceback
                traceback.print_exc()
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                
        logger.error(f"❌ Не удалось авторизоваться после {max_retries} попыток")
        return None, None
        
    finally:
        # Восстанавливаем прокси
        _enable_proxy()


def refresh_data():
    """Обновление данных из API (БЕЗ прокси)"""
    logger.info("🔄 Обновление данных из API...")
    
    # Отключаем прокси для API запросов
    _disable_proxy()
    
    try:
        session_id, contract_id = authenticate()
        if not session_id or not contract_id:
            logger.error("❌ Ошибка авторизации, обновление данных прервано")
            return False
        
        current_date = datetime.now()
        date_to = current_date.strftime("%Y-%m-%d")
        date_from = (current_date - timedelta(days=30)).strftime("%Y-%m-%d")
        
        headers = {
            'Content-Type': 'application/json',
            'api_key': api_key,
            'session_id': session_id,
            'contract_id': str(contract_id),
        }
        
        logger.info(f"📊 Параметры запроса: date_from={date_from}, date_to={date_to}")
        
        # Получаем транзакции (page_limit увеличен, чтобы не терять старые)
        logger.info("📊 Получение транзакций...")
        transactions = None
        for attempt in range(3):
            try:
                url_transactions = f'{url}/vip/v2/transactions?date_from={date_from}&date_to={date_to}&page_limit=500'
                
                response = requests.get(
                    url_transactions,
                    headers=headers,
                    timeout=getattr(config, 'API_TIMEOUT', 60),
                    verify=False
                )
                transactions = response.json()
                
                if transactions.get('status', {}).get('code') == 200:
                    result = transactions.get('data', {}).get('result', [])
                    logger.info(f"✅ Получено транзакций: {len(result)}")
                    # Показываем диапазон дат, который вернул API
                    if result:
                        dates = sorted(set(t.get('timestamp', '')[:10] for t in result if t.get('timestamp')))
                        logger.info(f"📅 Даты транзакций в API: {dates[0]} … {dates[-1]}")
                    break
                else:
                    logger.error(f"⚠️ Ошибка API: {transactions.get('status', {})}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Ошибка API: {transactions.get('status', {})}")
            except Exception as e:
                logger.error(f"⚠️ Ошибка получения транзакций, попытка {attempt+1}/3: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
        
        # Получаем данные контракта
        logger.info("📊 Получение данных контракта...")
        contract_data = None
        for attempt in range(3):
            try:
                url_contract = f'{url}/vip/v1/getPartContractData?contract_id={contract_id}'
                
                response = requests.get(
                    url_contract,
                    headers=headers,
                    timeout=getattr(config, 'API_TIMEOUT', 60),
                    verify=False
                )
                contract_data = response.json()
                
                if contract_data.get('status', {}).get('code') == 200:
                    balance = contract_data.get('data', {}).get('balanceData', {}).get('balance', 0)
                    logger.info(f"💰 Получен баланс: {balance}")
                    break
                else:
                    logger.error(f"⚠️ Ошибка API: {contract_data.get('status', {})}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Ошибка API: {contract_data.get('status', {})}")
            except Exception as e:
                logger.error(f"⚠️ Ошибка получения данных контракта, попытка {attempt+1}/3: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
        
        # Получаем данные карт
        logger.info("📊 Получение списка карт...")
        cards_data = None
        for attempt in range(3):
            try:
                url_cards = f'{url}/vip/v2/cards?status=Active&page=1&on_page=18'
                
                response = requests.get(
                    url_cards,
                    headers=headers,
                    timeout=getattr(config, 'API_TIMEOUT', 60),
                    verify=False
                )
                cards_data = response.json()
                
                if cards_data.get('status', {}).get('code') == 200:
                    cards_count = len(cards_data.get('data', {}).get('result', []))
                    logger.info(f"💳 Получено карт: {cards_count}")
                    break
                else:
                    logger.error(f"⚠️ Ошибка API: {cards_data.get('status', {})}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Ошибка API: {cards_data.get('status', {})}")
            except Exception as e:
                logger.error(f"⚠️ Ошибка получения данных карт, попытка {attempt+1}/3: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise
        
        if not transactions or not contract_data:
            raise Exception("Не удалось получить данные")
            
    except Exception as e:
        logger.error(f"❌ Ошибка API: {e}")
        return False
    finally:
        # Восстанавливаем прокси
        _enable_proxy()

    # Обработка баланса
    balance = 0
    try:
        balance = float(contract_data.get('data', {}).get('balanceData', {}).get('balance', 0))
        logger.info(f"💰 Получен баланс: {balance}")
    except Exception as e:
        logger.error(f"Ошибка обработки баланса: {e}")
    
    import db_helper
    db_helper.save_balance(balance)

    # Обработка карт
    cards_list = []
    try:
        cards_result = cards_data.get('data', {}).get('result', []) if cards_data else []
        for item in cards_result:
            if item.get('number'):
                cards_list.append({
                    'number': item.get('number'),
                    'comment': item.get('comment', '')
                })
        logger.info(f"💳 Сохранено {len(cards_list)} карт")
    except Exception as e:
        logger.error(f"Ошибка обработки списка карт: {e}")
    
    if cards_list:
        db_helper.save_cards(cards_list)

    # Обработка транзакций - используем сумму без скидки
    transactions_list = []
    try:
        result = transactions.get('data', {}).get('result', [])
        for item in sorted(result, key=lambda x: x.get('timestamp', ''), reverse=True):
            if item.get('timestamp') and item.get('card_number') and item.get('sum'):
                try:
                    # Используем сумму без скидки (без коэффициента)
                    cost = float(item.get('sum', 0))
                    transactions_list.append({
                        'timestamp': item['timestamp'],
                        'card_number': str(item['card_number']),
                        'cost': round(cost, 2),
                        'user_name': ''
                    })
                except (ValueError, TypeError) as e:
                    logger.error(f"Ошибка обработки транзакции: {e}")
        logger.info(f"📋 Сохранено {len(transactions_list)} транзакций")
    except Exception as e:
        logger.error(f"Ошибка обработки транзакций: {e}")

    if transactions_list:
        db_helper.save_transactions(transactions_list)

    db_helper.set_last_update_time()
    logger.info("✅ Данные успешно обновлены!")
    return True


def fetch_transaction_data():
    """Получение данных из БД для отображения"""
    import db_helper
    
    balance_data = db_helper.get_balance()
    balance = balance_data['balance'] if balance_data else 0
    transactions = db_helper.get_transactions(limit=10)
    cards = db_helper.get_cards()

    return {
        'time_stack': [t['timestamp'] for t in transactions],
        'card_number_stack': [t['card_number'] for t in transactions],
        'base_cost_5_stack': [t['cost'] for t in transactions],
        'balance': balance,
        'comment_stack': [c['comment'] for c in cards],
        'number_stack': [c['number'] for c in cards]
    }


def fetch_transaction_data_for_card(card_number, days=7):
    """Получение данных по конкретной карте"""
    import db_helper
    
    card_norm = str(card_number).replace(' ', '').replace('-', '').strip()
    transactions = db_helper.get_transactions_for_card(card_norm, days)
    return {
        'time_stack': [t['timestamp'] for t in transactions],
        'cost_stack': [t['cost'] for t in transactions]
    }


def get_user_name_by_card_simple(card_number):
    """Получение имени пользователя по карте"""
    try:
        with open('users.json', 'r', encoding='utf-8') as f:
            users = json.load(f)
        clean_card = str(card_number).replace(' ', '').replace('-', '').strip()
        for user_id, data in users.items():
            db_card = str(data.get('card_number', '')).replace(' ', '').replace('-', '').strip()
            if db_card == clean_card:
                return data.get('name', 'неизвестно')
    except Exception as e:
        logger.error(f"Ошибка чтения users.json: {e}")
    return 'неизвестно'
