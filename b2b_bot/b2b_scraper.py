import logging
import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

import config
import logger

logger_b2b = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "Наименование",
    "Компетенция",
    "Основные задачи",
    "Требования",
    "Дополнительные требования",
    "Срок привлечения",
]

DEBUG_DIR = "debug"

def _create_driver():
    """Создаёт драйвер Chrome для ARM."""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9222')
    options.binary_location = '/usr/bin/chromium'
    
    driver = webdriver.Chrome(options=options)
    return driver

async def _dump_debug(driver, name):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        driver.save_screenshot(os.path.join(DEBUG_DIR, f"{name}.png"))
    except Exception as e:
        logger_b2b.warning("Не удалось сохранить скриншот %s: %s", name, e)
    try:
        with open(os.path.join(DEBUG_DIR, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        logger_b2b.warning("Не удалось сохранить HTML %s: %s", name, e)

def _login(driver, login_, password):
    """Авторизация на B2B-Center."""
    try:
        logger_b2b.info("🔐 Открываем страницу входа...")
        driver.get('https://www.b2b-center.ru/login.html')
        time.sleep(3)

        # Ищем форму логина
        username_selectors = [
            'input[name="LoginForm[username]"]',
            'input[name="username"]',
            'input[type="text"]',
            'input[type="email"]',
        ]
        
        username_field = None
        for selector in username_selectors:
            try:
                username_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if username_field:
                    logger_b2b.info(f"✅ Найдено поле username: {selector}")
                    break
            except:
                continue

        if not username_field:
            _dump_debug(driver, "no_username_field")
            raise Exception("Не найдено поле для ввода логина")

        username_field.send_keys(login_)

        password_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
        )
        password_field.send_keys(password)

        # Нажимаем кнопку входа
        submit = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        submit.click()
        time.sleep(5)

        # Проверяем успешность входа
        if 'login' not in driver.current_url:
            logger_b2b.info(f"✅ Авторизация успешна! Текущий URL: {driver.current_url}")
            return
        else:
            _dump_debug(driver, "login_failed")
            raise Exception("Не удалось авторизоваться на B2B-Center")

    except Exception as e:
        logger_b2b.error(f"❌ Ошибка авторизации: {e}")
        _dump_debug(driver, "login_error")
        raise

def _scrape_one_procedure(driver, link):
    """Открывает процедуру и вытаскивает таблицу закупочных позиций."""
    results = []
    logger_b2b.info(f"📑 Открываем процедуру: {link}")
    driver.get(link)
    time.sleep(5)

    # Ищем вкладку "Закупочные позиции"
    try:
        tab = driver.find_element(By.PARTIAL_LINK_TEXT, "Закупочные позиции")
        tab.click()
        time.sleep(3)
    except:
        logger_b2b.warning("Вкладка 'Закупочные позиции' не найдена")

    # Ищем таблицу
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
    except:
        logger_b2b.warning("Таблица не найдена")
        return results

    rows = driver.find_elements(By.CSS_SELECTOR, 'table tr')
    for row in rows[1:]:
        cells = row.find_elements(By.TAG_NAME, 'td')
        if len(cells) >= 6:
            row_data = [cell.text.strip() for cell in cells[:6]]
            if row_data and row_data[0].isdigit():
                results.append(row_data)

    return results

async def scrape_procedures(links, b2b_login, b2b_password):
    """Парсит все ссылки за один сеанс."""
    results_by_link = {}
    driver = _create_driver()
    try:
        _login(driver, b2b_login, b2b_password)
        for link in links:
            results_by_link[link] = _scrape_one_procedure(driver, link)
    finally:
        driver.quit()
    return results_by_link

async def scrape_positions(link, b2b_login, b2b_password):
    results = await scrape_procedures([link], b2b_login, b2b_password)
    return results.get(link, [])
