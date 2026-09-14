"""
Failover-транспорт для httpx: перебирает список прокси по очереди,
пока один из них не ответит успешно. Используется python-telegram-bot
(HTTPXRequest) для запросов к Telegram Bot API, чтобы кратковременная
недоступность одной "коробки" с SOCKS5-прокси не роняла все запросы бота
(в первую очередь long-polling get_updates, который стучится непрерывно).
"""

import logging
import httpx

logger = logging.getLogger(__name__)


class FailoverProxyTransport(httpx.AsyncBaseTransport):
    """
    httpx.AsyncBaseTransport, который на каждый запрос пробует прокси
    из списка `proxies` по порядку, пока один не сработает.

    Порядок в списке = приоритет: первый прокси используется как основной,
    остальные — как резервные. Если основной снова "оживёт", запросы сами
    вернутся к нему на следующей попытке (никакого залипания на резервном).
    """

    # Ошибки, при которых имеет смысл пробовать следующий прокси,
    # а не сразу отдавать исключение наверх.
    RETRIABLE_EXCEPTIONS = (
        httpx.ProxyError,
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )

    def __init__(self, proxies: list[str], **transport_kwargs):
        if not proxies:
            raise ValueError("FailoverProxyTransport: список прокси пуст")
        self._proxy_urls = proxies
        self._transports = [
            httpx.AsyncHTTPTransport(proxy=p, **transport_kwargs) for p in proxies
        ]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None

        for idx, transport in enumerate(self._transports):
            try:
                response = await transport.handle_async_request(request)
                if idx > 0:
                    logger.warning(
                        "Запрос выполнен через резервный прокси #%s (%s), "
                        "основной прокси недоступен",
                        idx, self._proxy_urls[idx],
                    )
                return response
            except self.RETRIABLE_EXCEPTIONS as e:
                last_exc = e
                logger.warning(
                    "Прокси %s недоступен (%s: %s), пробую следующий",
                    self._proxy_urls[idx], type(e).__name__, e,
                )
                continue

        # Все прокси из списка не сработали — пробрасываем последнюю ошибку,
        # дальше её подхватит error_handler бота как обычно.
        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()
