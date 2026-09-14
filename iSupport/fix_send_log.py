import re

with open('bot.py', 'r') as f:
    content = f.read()

# Исправляем f-string с вложенными кавычками
old = 'url = f"{CUSTOM_API_URL.rstrip("/")}/bot{TOKEN}/sendMessage"'
new = 'url = f"{CUSTOM_API_URL.rstrip(\'/\')}/bot{TOKEN}/sendMessage"'
content = content.replace(old, new)

with open('bot.py', 'w') as f:
    f.write(content)

print("✅ Исправлено")
