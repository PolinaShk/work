#!/bin/bash

echo "🚀 Запуск B2B бота..."

if [ ! -f "/app/service_account.json" ]; then
    echo "❌ Файл service_account.json не найден! Положите его рядом с ботом (см. README.md)"
    exit 1
fi

if [ ! -f "/app/.env" ]; then
    echo "❌ Файл .env не найден! Скопируйте .env.example в .env и заполните значения"
    exit 1
fi

echo "📌 Запуск main.py..."
python3 /app/main.py
