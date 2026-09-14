# -*- coding: utf-8 -*-
import sqlite3
import json
import time
import os
from datetime import datetime

DB_FILE = "fuel_data.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            card_number TEXT,
            cost REAL,
            user_name TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS balance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance REAL,
            updated_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS cards (
            card_number TEXT PRIMARY KEY,
            comment TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        # Индекс для быстрого поиска по карте и дате
        c.execute('''CREATE INDEX IF NOT EXISTS idx_transactions_card ON transactions(card_number)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_transactions_ts ON transactions(timestamp)''')
        conn.commit()
        conn.close()
        print(f"✅ База данных создана: {DB_FILE}")
        return True
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        return False


def save_transactions(transactions_list):
    """
    Сохраняет транзакции, НЕ удаляя старые.
    Дубликаты (по timestamp + card_number) не добавляются повторно.
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        added = 0
        for tx in transactions_list:
            c.execute("""INSERT INTO transactions (timestamp, card_number, cost, user_name)
                         SELECT ?, ?, ?, ?
                         WHERE NOT EXISTS (
                             SELECT 1 FROM transactions
                             WHERE timestamp = ? AND card_number = ?
                         )""",
                      (tx['timestamp'], tx['card_number'], tx['cost'], tx.get('user_name', ''),
                       tx['timestamp'], tx['card_number']))
            if c.rowcount > 0:
                added += 1
        conn.commit()
        conn.close()
        print(f"✅ Добавлено новых транзакций: {added} (из {len(transactions_list)} полученных)")
    except Exception as e:
        print(f"❌ Ошибка сохранения транзакций: {e}")


def save_balance(balance):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM balance")
        c.execute("INSERT INTO balance (balance, updated_at) VALUES (?, ?)",
                  (balance, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        print(f"✅ Сохранен баланс: {balance}")
    except Exception as e:
        print(f"❌ Ошибка сохранения баланса: {e}")


def save_cards(cards_list):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM cards")
        for card in cards_list:
            c.execute("INSERT OR REPLACE INTO cards (card_number, comment) VALUES (?, ?)",
                      (card['number'], card.get('comment', '')))
        conn.commit()
        conn.close()
        print(f"✅ Сохранено {len(cards_list)} карт")
    except Exception as e:
        print(f"❌ Ошибка сохранения карт: {e}")


def get_balance():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("SELECT balance, updated_at FROM balance ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if row:
            return {'balance': row[0], 'updated_at': row[1]}
    except Exception as e:
        print(f"❌ Ошибка получения баланса: {e}")
    return None


def get_transactions(limit=10):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("SELECT timestamp, card_number, cost, user_name FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        return [{'timestamp': r[0], 'card_number': r[1], 'cost': r[2], 'user_name': r[3]} for r in rows]
    except Exception as e:
        print(f"❌ Ошибка получения транзакций: {e}")
        return []


def get_transactions_for_card(card_number, days=7):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("""SELECT timestamp, cost FROM transactions 
                     WHERE card_number = ? 
                     AND timestamp >= datetime('now', ?) 
                     ORDER BY timestamp DESC""",
                  (card_number, f'-{days} days'))
        rows = c.fetchall()
        conn.close()
        return [{'timestamp': r[0], 'cost': r[1]} for r in rows]
    except Exception as e:
        print(f"❌ Ошибка получения транзакций для карты: {e}")
        return []


def get_cards():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("SELECT card_number, comment FROM cards")
        rows = c.fetchall()
        conn.close()
        return [{'number': r[0], 'comment': r[1]} for r in rows]
    except Exception as e:
        print(f"❌ Ошибка получения карт: {e}")
        return []


def get_last_update_time():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("SELECT value FROM meta WHERE key='last_update'")
        row = c.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception as e:
        print(f"❌ Ошибка получения времени обновления: {e}")
    return "никогда"


def set_last_update_time():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_update', ?)",
                  (datetime.now().isoformat(),))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка сохранения времени обновления: {e}")
