FROM python:3.11-slim

WORKDIR /app

# Устанавливаем минимальные зависимости
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# ОБНОВЛЯЕМ PIP И УСТАНАВЛИВАЕМ ЗАВИСИМОСТИ
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
