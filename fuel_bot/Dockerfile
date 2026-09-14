FROM python:3.11-slim

WORKDIR /app

# Устанавливаем cron и время
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY *.py ./
COPY *.json ./
COPY *.db ./

# Настраиваем cron
COPY crontab /etc/cron.d/fuel-bot
RUN chmod 0644 /etc/cron.d/fuel-bot
RUN crontab /etc/cron.d/fuel-bot

# Скрипт для запуска
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
