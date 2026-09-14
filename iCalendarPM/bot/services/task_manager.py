import logging
from datetime import datetime
from sqlalchemy import select, desc, text
from bot.database import async_session
from bot.logger import send_log, send_error, send_success, send_info

logger = logging.getLogger(__name__)

class TaskManager:
    """Сервис для управления задачами с историей изменений"""
    
    @staticmethod
    async def create_task(task_id: str, title: str, description: str, created_by: int) -> dict:
        """Создание новой задачи"""
        async with async_session() as session:
            # Проверяем, не существует ли уже такой task_id
            result = await session.execute(
                text("SELECT id FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id}
            )
            existing = result.fetchone()
            if existing:
                return {"success": False, "error": f"Задача {task_id} уже существует"}
            
            # Создаём задачу
            await session.execute(
                text("""
                    INSERT INTO tasks (task_id, title, description, status, created_by)
                    VALUES (:task_id, :title, :description, 'новая', :created_by)
                """),
                {"task_id": task_id, "title": title, "description": description, "created_by": created_by}
            )
            
            # Добавляем запись в историю
            await session.execute(
                text("""
                    INSERT INTO task_history (task_id, changed_by, field_name, old_value, new_value, comment)
                    VALUES (:task_id, :changed_by, 'создание', NULL, :new_value, 'Задача создана')
                """),
                {"task_id": task_id, "changed_by": created_by, "new_value": title}
            )
            
            await session.commit()
            
            # Логируем в беседу
            await send_log(f"📋 Новая задача: {task_id} - {title} (создал пользователь {created_by})")
            
            return {"success": True, "task_id": task_id}
    
    @staticmethod
    async def update_task_status(task_id: str, new_status: str, changed_by: int, comment: str = None) -> dict:
        """Обновление статуса задачи с записью в историю"""
        async with async_session() as session:
            # Получаем текущий статус
            result = await session.execute(
                text("SELECT status FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id}
            )
            row = result.fetchone()
            if not row:
                return {"success": False, "error": f"Задача {task_id} не найдена"}
            
            old_status = row[0]
            
            if old_status == new_status:
                return {"success": False, "error": f"Статус уже '{new_status}'"}
            
            # Обновляем статус
            await session.execute(
                text("UPDATE tasks SET status = :status WHERE task_id = :task_id"),
                {"status": new_status, "task_id": task_id}
            )
            
            # Добавляем запись в историю
            await session.execute(
                text("""
                    INSERT INTO task_history (task_id, changed_by, field_name, old_value, new_value, comment)
                    VALUES (:task_id, :changed_by, 'статус', :old_value, :new_value, :comment)
                """),
                {
                    "task_id": task_id,
                    "changed_by": changed_by,
                    "old_value": old_status,
                    "new_value": new_status,
                    "comment": comment or f"Изменение статуса"
                }
            )
            
            await session.commit()
            
            # Логируем в беседу
            user_info = await TaskManager._get_user_info(changed_by)
            await send_log(
                f"📌 Изменение статуса задачи {task_id}: {old_status} → {new_status} "
                f"(пользователь: {user_info})"
            )
            
            return {"success": True, "old_status": old_status, "new_status": new_status}
    
    @staticmethod
    async def add_comment(task_id: str, author_id: int, comment: str) -> dict:
        """Добавление комментария к задаче"""
        async with async_session() as session:
            # Проверяем существование задачи
            result = await session.execute(
                text("SELECT id FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id}
            )
            if not result.fetchone():
                return {"success": False, "error": f"Задача {task_id} не найдена"}
            
            # Добавляем комментарий
            await session.execute(
                text("""
                    INSERT INTO task_comments (task_id, author_id, comment)
                    VALUES (:task_id, :author_id, :comment)
                """),
                {"task_id": task_id, "author_id": author_id, "comment": comment}
            )
            
            await session.commit()
            
            # Логируем в беседу
            user_info = await TaskManager._get_user_info(author_id)
            await send_log(f"💬 Комментарий к задаче {task_id} от {user_info}: {comment[:100]}...")
            
            return {"success": True, "task_id": task_id}
    
    @staticmethod
    async def get_task_history(task_id: str) -> list:
        """Получение полной истории изменений задачи"""
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT 
                        h.changed_at,
                        h.changed_by,
                        h.field_name,
                        h.old_value,
                        h.new_value,
                        h.comment,
                        u.username,
                        u.first_name
                    FROM task_history h
                    LEFT JOIN users u ON u.telegram_id = h.changed_by
                    WHERE h.task_id = :task_id
                    ORDER BY h.changed_at DESC
                """),
                {"task_id": task_id}
            )
            
            history = []
            for row in result:
                user_name = row[6] or row[7] or str(row[1])
                history.append({
                    "changed_at": row[0].strftime("%d.%m.%Y %H:%M:%S") if row[0] else "",
                    "changed_by": row[1],
                    "changed_by_name": user_name,
                    "field_name": row[2],
                    "old_value": row[3] or "",
                    "new_value": row[4] or "",
                    "comment": row[5] or ""
                })
            
            return history
    
    @staticmethod
    async def get_task_comments(task_id: str) -> list:
        """Получение всех комментариев к задаче"""
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT 
                        c.created_at,
                        c.author_id,
                        c.comment,
                        u.username,
                        u.first_name
                    FROM task_comments c
                    LEFT JOIN users u ON u.telegram_id = c.author_id
                    WHERE c.task_id = :task_id
                    ORDER BY c.created_at ASC
                """),
                {"task_id": task_id}
            )
            
            comments = []
            for row in result:
                user_name = row[3] or row[4] or str(row[1])
                comments.append({
                    "created_at": row[0].strftime("%d.%m.%Y %H:%M:%S") if row[0] else "",
                    "author_id": row[1],
                    "author_name": user_name,
                    "comment": row[2]
                })
            
            return comments
    
    @staticmethod
    async def get_all_tasks(limit: int = 20) -> list:
        """Получение всех задач с последним статусом"""
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT 
                        t.task_id,
                        t.title,
                        t.description,
                        t.status,
                        t.created_at,
                        t.created_by,
                        u.username,
                        u.first_name
                    FROM tasks t
                    LEFT JOIN users u ON u.telegram_id = t.created_by
                    ORDER BY t.created_at DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            )
            
            tasks = []
            for row in result:
                user_name = row[6] or row[7] or str(row[5])
                tasks.append({
                    "task_id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "status": row[3],
                    "created_at": row[4].strftime("%d.%m.%Y %H:%M:%S") if row[4] else "",
                    "created_by": row[5],
                    "created_by_name": user_name
                })
            
            return tasks
    
    @staticmethod
    async def _get_user_info(user_id: int) -> str:
        """Получение информации о пользователе по ID"""
        async with async_session() as session:
            result = await session.execute(
                text("SELECT username, first_name FROM users WHERE telegram_id = :user_id"),
                {"user_id": user_id}
            )
            row = result.fetchone()
            if row:
                return row[0] or row[1] or str(user_id)
            return str(user_id)


# ========== SQL для создания таблиц ==========

CREATE_TABLES_SQL = """
-- Таблица задач
CREATE TABLE IF NOT EXISTS tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(50) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'новая',
    created_by INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Таблица истории изменений
CREATE TABLE IF NOT EXISTS task_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(50) NOT NULL,
    changed_by INT NOT NULL,
    field_name VARCHAR(50) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    comment TEXT
);

-- Таблица комментариев
CREATE TABLE IF NOT EXISTS task_comments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(50) NOT NULL,
    author_id INT NOT NULL,
    comment TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
