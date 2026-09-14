import socks
import socket
import httpx

PROXY = "socks5://192.168.1.141:8085"

# 1. Проверка через pysocks
try:
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, "192.168.1.141", 8085)
    s.connect(("api.telegram.org", 443))
    print("✅ pysocks работает")
    s.close()
except Exception as e:
    print(f"❌ pysocks ошибка: {e}")

# 2. Проверка через httpx
try:
    transport = httpx.HTTPTransport(proxy=PROXY)
    client = httpx.Client(transport=transport)
    r = client.get("https://api.telegram.org")
    print(f"✅ httpx работает: {r.status_code}")
except Exception as e:
    print(f"❌ httpx ошибка: {e}")