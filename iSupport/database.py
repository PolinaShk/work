# -*- coding: utf-8 -*-
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import shutil
import pickle
from datetime import datetime
import config
import time

# ОТКЛЮЧАЕМ ПРОКСИ ДЛЯ GOOGLE SHEETS
for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(var, None)

class Database:
    def __init__(self):
        print("Init database...")
        print(f"SHEET_ID: {config.SHEET_ID}")

        scope = ["https://spreadsheets.google.com/feeds"]

        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            self.client = gspread.authorize(creds)
            print("Auth OK (Service Account)")
        except Exception as e:
            print(f"Auth error: {e}")
            raise

        try:
            print(f"Opening sheet: {config.SHEET_ID}")
            self.sheet = self.client.open_by_key(config.SHEET_ID)
            print(f"SHEET FOUND: {self.sheet.title}")
        except Exception as e:
            print(f"ERROR: {e}")
            raise

        self._init_sheets()
        self.last_status = {}
        
        # КЭШИ ДЛЯ УМЕНЬШЕНИЯ ЗАПРОСОВ К GOOGLE SHEETS
        self._admin_cache = None
        self._admin_cache_time = 0
        self._categories_cache = None
        self._categories_cache_time = 0
        self._users_cache = None
        self._users_cache_time = 0
        self._resources_cache = None
        self._resources_cache_time = 0

        # ПРИ ЗАПУСКЕ УСТАНАВЛИВАЕМ ВСЕМ РЕСУРСАМ СТАТУС OK
        self._init_resource_statuses()

    def _init_resource_statuses(self):
        """При запуске устанавливаем всем ресурсам статус OK, чтобы бот знал baseline"""
        resources = self.get_resources()
        for res in resources:
            self.last_status[res["address"]] = "OK"
            print(f'✅ {res["description"]}: установлен OK')
        print(f"✅ Все ресурсы инициализированы со статусом OK")

    def _init_sheets(self):
        # Таблица задач
        try:
            worksheet = self.sheet.worksheet("tasks")
            headers = worksheet.row_values(1)
            if "История" not in headers:
                worksheet.add_cols(1)
                headers.append("История")
                worksheet.update("A1:J1", [["ID", "User ID", "Username", "Категория", "Текст", "Дата создания", "Статус", "Комментарий админа", "Файлы", "История"]])
        except:
            tasks = self.sheet.add_worksheet("tasks", 1, 10)
            tasks.update("A1:J1", [["ID", "User ID", "Username", "Категория", "Текст", "Дата создания", "Статус", "Комментарий админа", "Файлы", "История"]])
            print("Created tasks sheet")

        # Таблица логов
        try:
            self.sheet.worksheet("logs")
        except:
            logs = self.sheet.add_worksheet("logs", 1, 4)
            logs.update("A1:D1", [["Адрес", "Время", "Статус", "Таймаут"]])
            print("Created logs sheet")

        # Таблица админов
        try:
            self.sheet.worksheet("admin")
        except:
            admin = self.sheet.add_worksheet("admin", 1, 2)
            admin.update("A1:B1", [["ID", "Name"]])
            print("Created admin sheet")

        # Таблица пользователей
        try:
            self.sheet.worksheet("users")
        except:
            users = self.sheet.add_worksheet("users", 1, 3)
            users.update("A1:C1", [["ID", "Username", "Дата подтверждения"]])
            print("Created users sheet")

    # ========== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ (С КЭШИРОВАНИЕМ) ==========
    def add_user(self, user_id, username):
        try:
            worksheet = self.sheet.worksheet("users")
            data = worksheet.get_all_values()
            for row in data[1:]:
                if row and row[0] == str(user_id):
                    return True

            now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            worksheet.append_row([str(user_id), username, now])
            # Сбрасываем кэш пользователей
            self._users_cache = None
            return True
        except Exception as e:
            print(f"Add user error: {e}")
            return False

    def is_user_exists(self, user_id):
        try:
            # Используем кэш
            if self._users_cache is None:
                worksheet = self.sheet.worksheet("users")
                data = worksheet.get_all_values()
                self._users_cache = [row[0] for row in data[1:] if row and row[0]]
            
            return str(user_id) in self._users_cache
        except Exception as e:
            print(f"Check user error: {e}")
            return False

    def add_admin(self, user_id, username):
        try:
            worksheet = self.sheet.worksheet("admin")
            data = worksheet.get_all_values()
            for row in data[1:]:
                if row and row[0] == str(user_id):
                    return True
            worksheet.append_row([str(user_id), username])
            # Сбрасываем кэш админов
            self._admin_cache = None
            return True
        except Exception as e:
            print(f"Add admin error: {e}")
            return False

    def get_problem_categories(self):
        """Кэширует категории проблем на 5 минут"""
        now = time.time()
        if self._categories_cache is not None and now - self._categories_cache_time < 300:
            return self._categories_cache

        try:
            worksheet = self.sheet.worksheet("resourse")
            data = worksheet.col_values(12)
            categories = []
            for item in data:
                if item and item.strip():
                    categories.append(item.strip())

            seen = set()
            unique_categories = []
            for cat in categories:
                if cat not in seen:
                    seen.add(cat)
                    unique_categories.append(cat)
            
            self._categories_cache = unique_categories
            self._categories_cache_time = now
            return unique_categories
        except Exception as e:
            print(f"Ошибка получения категорий проблем: {e}")
            return self._categories_cache or []

    def get_stats_period(self):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            if len(data) <= 1:
                return None

            dates = []
            for row in data[1:]:
                if row and len(row) > 5 and row[5]:
                    dates.append(row[5])

            if not dates:
                return None

            parsed_dates = []
            for date_str in dates:
                try:
                    dt = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                    parsed_dates.append(dt)
                except:
                    pass

            if not parsed_dates:
                return None

            first_date = min(parsed_dates)
            last_date = max(parsed_dates)

            return {
                'first_date': first_date.strftime("%d.%m.%Y %H:%M"),
                'last_date': last_date.strftime("%d.%m.%Y %H:%M"),
                'total': len(parsed_dates)
            }
        except Exception as e:
            print(f"Get stats period error: {e}")
            return None

    def get_last_known_status(self, resource):
        return self.last_status.get(resource, "UNKNOWN")

    def set_last_known_status(self, resource, status):
        self.last_status[resource] = status

    def save_file_locally(self, task_id, file_name, file_bytes):
        try:
            task_folder = os.path.join(config.FILES_STORAGE_PATH, f"task_{task_id}")
            os.makedirs(task_folder, exist_ok=True)
            file_path = os.path.join(task_folder, file_name)

            with open(file_path, 'wb') as f:
                f.write(file_bytes)

            return file_path
        except Exception as e:
            print(f"Error saving file locally: {e}")
            return None

    def get_task_files(self, task_id):
        task_folder = os.path.join(config.FILES_STORAGE_PATH, f"task_{task_id}")
        if not os.path.exists(task_folder):
            return []

        files = []
        for f in os.listdir(task_folder):
            file_path = os.path.join(task_folder, f)
            if os.path.isfile(file_path):
                files.append({
                    "name": f,
                    "path": file_path,
                    "size": os.path.getsize(file_path),
                    "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%d.%m.%Y %H:%M")
                })
        return files

    def delete_task_files(self, task_id):
        task_folder = os.path.join(config.FILES_STORAGE_PATH, f"task_{task_id}")
        if os.path.exists(task_folder):
            shutil.rmtree(task_folder)
            return True
        return False

    def get_admin_list(self):
        """Кэширует список админов на 60 секунд"""
        now = time.time()
        if self._admin_cache is not None and now - self._admin_cache_time < 60:
            return self._admin_cache

        try:
            worksheet = self.sheet.worksheet("admin")
            data = worksheet.get_all_values()
            admins = []
            for row in data[1:]:
                if row and row[0] and row[0] != "ID":
                    admins.append({"id": int(row[0]), "name": row[1] if len(row) > 1 else "Admin"})
            self._admin_cache = admins
            self._admin_cache_time = now
            return admins
        except:
            return self._admin_cache or []

    def is_admin(self, user_id):
        admins = self.get_admin_list()
        return any(admin["id"] == user_id for admin in admins)

    def get_admin_name(self, user_id):
        admins = self.get_admin_list()
        for admin in admins:
            if admin["id"] == user_id:
                return admin["name"]
        return f"User_{user_id}"

    def add_history(self, task_id, action, user_id, username):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()
            now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            history_entry = f"[{now}] {username} (ID:{user_id}): {action}"

            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == str(task_id):
                    old_history = row[9] if len(row) > 9 else ""
                    new_history = f"{old_history}\n{history_entry}" if old_history else history_entry
                    worksheet.update(f"J{i}", new_history)
                    return True
            return False
        except Exception as e:
            print(f"History error: {e}")
            return False

    def create_task(self, user_id, username, text, category, problem_category="", files=None, file_links=None):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            new_id = 1
            if len(data) > 1:
                ids = [int(row[0]) for row in data[1:] if row and row[0].isdigit()]
                new_id = max(ids) + 1 if ids else 1

            now = datetime.now().strftime("%d.%m.%Y %H:%M")
            category_name = config.CATEGORY_NAMES.get(category, "Другое")
            full_category = f"{problem_category} → {category_name}" if problem_category else category_name

            files_str = ""
            if file_links:
                files_str = "\n".join([f"📎 {link}" for link in file_links])
            elif files:
                files_str = "\n".join(files)

            worksheet.append_row([
                str(new_id),
                str(user_id),
                username,
                full_category,
                text,
                now,
                "Новое",
                "",
                files_str,
                f"[{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}] {username} (ID:{user_id}): Создал задачу"
            ])

            return new_id
        except Exception as e:
            print(f"Create task error: {e}")
            return None

    def change_task_status(self, task_id, new_status, admin_id, admin_name):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == str(task_id):
                    old_status = row[6] if len(row) > 6 else "Новое"
                    worksheet.update(f"G{i}", new_status)

                    action = f"Изменил статус с '{old_status}' на '{new_status}'"
                    self.add_history(task_id, action, admin_id, admin_name)

                    return int(row[1]), True
            return None, False
        except Exception as e:
            print(f"Change status error: {e}")
            return None, False

    def add_comment(self, task_id, comment, admin_id, admin_name):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()
            now = datetime.now().strftime("%d.%m.%Y %H:%M")

            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == str(task_id):
                    old_comment = row[7] if len(row) > 7 else ""
                    new_comment = f"{old_comment}\n[{now}] {comment}" if old_comment else f"[{now}] {comment}"
                    worksheet.update(f"H{i}", new_comment)

                    action = f"Добавил комментарий: {comment}"
                    self.add_history(task_id, action, admin_id, admin_name)

                    return int(row[1]), True
            return None, False
        except Exception as e:
            print(f"Add comment error: {e}")
            return None, False

    def change_category(self, task_id, new_category, admin_id, admin_name):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == str(task_id):
                    old_category = row[3] if len(row) > 3 else "Другое"
                    worksheet.update(f"D{i}", new_category)

                    action = f"Изменил категорию с '{old_category}' на '{new_category}'"
                    self.add_history(task_id, action, admin_id, admin_name)

                    return int(row[1]), True
            return None, False
        except Exception as e:
            print(f"Change category error: {e}")
            return None, False

    def get_task(self, task_id):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            for row in data[1:]:
                if row and row[0] == str(task_id):
                    return {
                        "id": int(row[0]),
                        "user_id": int(row[1]),
                        "username": row[2],
                        "category": row[3],
                        "text": row[4],
                        "date": row[5],
                        "status": row[6],
                        "comment": row[7] if len(row) > 7 else "",
                        "files": row[8] if len(row) > 8 else "",
                        "history": row[9] if len(row) > 9 else "Нет истории"
                    }
            return None
        except Exception as e:
            print(f"Get task error: {e}")
            return None

    def get_user_tasks(self, user_id, limit=10):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            tasks = []
            for row in data[1:]:
                if row and row[1] == str(user_id):
                    tasks.append({
                        "id": int(row[0]),
                        "category": row[3],
                        "text": row[4],
                        "status": row[6],
                        "date": row[5]
                    })
                    if len(tasks) >= limit:
                        break

            tasks.sort(key=lambda x: x['id'])
            return tasks
        except Exception as e:
            print(f"Get user tasks error: {e}")
            return []

    def get_all_tasks(self, limit=100):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            tasks = []
            for row in data[1:]:
                if row:
                    tasks.append({
                        "id": int(row[0]),
                        "user_id": int(row[1]),
                        "username": row[2],
                        "category": row[3],
                        "text": row[4],
                        "status": row[6],
                        "date": row[5]
                    })

            tasks.sort(key=lambda x: x['id'])

            if len(tasks) > limit:
                tasks = tasks[:limit]
            return tasks
        except Exception as e:
            print(f"Get all tasks error: {e}")
            return []

    def get_task_stats(self):
        try:
            worksheet = self.sheet.worksheet("tasks")
            data = worksheet.get_all_values()

            stats = {
                "Новое": 0,
                "На рассмотрении": 0,
                "В работе": 0,
                "Исправлено": 0,
                "Отклонено": 0
            }

            categories = {}

            for row in data[1:]:
                if row:
                    status = row[6] if len(row) > 6 else "Новое"
                    if status in stats:
                        stats[status] += 1

                    category = row[3] if len(row) > 3 else "Другое"
                    categories[category] = categories.get(category, 0) + 1

            return stats, categories
        except Exception as e:
            print(f"Get stats error: {e}")
            return {}, {}

    def get_resources(self):
        """Кэширует список ресурсов на 5 минут"""
        now = time.time()
        if self._resources_cache is not None and now - self._resources_cache_time < 300:
            return self._resources_cache

        try:
            worksheet = self.sheet.worksheet("resourse")
            data = worksheet.get_all_values()
            resources = []

            for row in data[1:]:
                if row and row[0]:
                    address = row[0].strip().rstrip('/')
                    resources.append({
                        "address": address,
                        "type": row[1].lower() if len(row) > 1 else "http",
                        "port": int(row[2]) if len(row) > 2 and row[2] else None,
                        "description": row[3] if len(row) > 3 else address
                    })
            
            self._resources_cache = resources
            self._resources_cache_time = now
            return resources
        except:
            return self._resources_cache or []

    def save_log(self, resource, status, timeout=None):
        try:
            worksheet = self.sheet.worksheet("logs")
            now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            worksheet.append_row([resource, now, status, timeout or ""])
        except Exception as e:
            print(f"Save log error: {e}")
            return False
        return True

    def get_logs(self, limit=10):
        try:
            worksheet = self.sheet.worksheet("logs")
            data = worksheet.get_all_values()
            logs = []

            for row in reversed(data[1:]):
                if row and row[0]:
                    logs.append({
                        "resource": row[0],
                        "time": row[1],
                        "status": row[2]
                    })
                    if len(logs) >= limit:
                        break
            return logs
        except:
            return []

    def get_last_status(self, resource):
        try:
            worksheet = self.sheet.worksheet("logs")
            data = worksheet.get_all_values()

            for row in reversed(data[1:]):
                if row and row[0] == resource:
                    return row[2] if len(row) > 2 else "UNKNOWN"
            return "UNKNOWN"
        except:
            return "UNKNOWN"
