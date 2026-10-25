"""Отправка сообщений в чаты. Настоящего мессенджера нет: заглушка только запоминает, что отправлено."""

from typing import Protocol


class Sender(Protocol):
    def send(self, chat_id: str, text: str) -> None:
        """Отправляет текст в чат от имени общего аккаунта."""
        ...


class StubSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))
