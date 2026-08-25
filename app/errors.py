from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass
class AppError(Exception):
    message: str
    code: str = "app_error"
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


class ValidationError(AppError):
    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message=message, code=code, status_code=400)


class PermissionDenied(AppError):
    def __init__(self, message: str = "У тебя недостаточно прав для этого действия."):
        super().__init__(message=message, code="permission_denied", status_code=403)


class NotFoundError(AppError):
    def __init__(self, message: str, code: str = "not_found"):
        super().__init__(message=message, code=code, status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str, code: str = "conflict"):
        super().__init__(message=message, code=code, status_code=409)


class ConfigurationError(AppError):
    def __init__(self, message: str):
        super().__init__(message=message, code="configuration_error", status_code=503)


class ExternalServiceError(AppError):
    def __init__(self, message: str = "VK временно не отвечает. Попробуй ещё раз через несколько секунд."):
        super().__init__(message=message, code="vk_unavailable", status_code=502)


def error_reference() -> str:
    return f"ERR-{uuid4().hex[:8].upper()}"


def vk_error_message(exc: Exception) -> str:
    """Translate common VK API failures into user-facing Russian text."""
    code = getattr(exc, "code", None)
    text = str(getattr(exc, "error_msg", "") or exc).lower()

    if code == 5:
        return "Токен сообщества VK недействителен или был отозван. Обнови VK_GROUP_TOKEN."
    if code == 6:
        return "VK временно ограничил частоту запросов. Повтори действие через несколько секунд."
    if code == 7:
        return "У сообщества не хватает прав VK для этого действия. Проверь права токена и сообщества."
    if code == 15:
        return "VK запретил доступ к этому объекту. Возможно, профиль закрыт или недоступен."
    if code == 100:
        if "longpoll" in text:
            return "Long Poll API для сообщества выключен. Включи его в Управление → Работа с API → Long Poll API."
        return "VK отклонил один из параметров запроса. Проверь введённые данные и настройки сообщества."
    if code in (901, 902):
        return "Не удалось отправить сообщение пользователю из-за настроек приватности VK."
    if code == 917:
        return "Не удалось добавить пользователя: VK ограничил это действие для данной беседы."
    return "VK не смог выполнить запрос. Попробуй ещё раз; если ошибка повторится — проверь логи бота."
