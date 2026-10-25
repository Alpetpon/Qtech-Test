"""Отправка сообщений в чаты. Настоящего мессенджера нет: заглушка только запоминает, что отправлено."""

from collections.abc import Callable
from typing import Protocol


class RateLimited(Exception):
    """Мессенджер ответил RATE_LIMIT: сообщение не отправлено, аккаунту надо подождать."""


class Sender(Protocol):
    def send(self, chat_id: str, text: str) -> None:
        """Отправляет текст в чат от имени общего аккаунта. RateLimited — не отправлено."""
        ...


class StubSender:
    """rate_limited по номеру попытки отправки (с 1) решает, ответить ли RATE_LIMIT."""

    def __init__(self, rate_limited: Callable[[int], bool] = lambda attempt: False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.attempts = 0
        self.rate_limited = rate_limited

    def send(self, chat_id: str, text: str) -> None:
        self.attempts += 1
        if self.rate_limited(self.attempts):
            raise RateLimited
        self.sent.append((chat_id, text))
