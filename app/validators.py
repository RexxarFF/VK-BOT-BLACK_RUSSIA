from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from .errors import ValidationError

_NICK_RE = re.compile(r'^[A-Za-z0-9_]{3,32}$')
_SCREEN_RE = re.compile(r'^[A-Za-z0-9_.]{2,64}$')


def clean_text(value: str, *, field: str, min_len: int = 1, max_len: int = 500) -> str:
    value = (value or '').strip()
    if len(value) < min_len:
        raise ValidationError(f'Поле «{field}» заполнено слишком коротко.')
    if len(value) > max_len:
        raise ValidationError(f'Поле «{field}» слишком длинное. Максимум: {max_len} символов.')
    return value


def validate_nickname(value: str) -> str:
    value = (value or '').strip()
    if not _NICK_RE.fullmatch(value):
        raise ValidationError(
            'Nick_Name должен содержать 3–32 символа: латинские буквы, цифры и _. Например: Felix_Wraith.'
        )
    return value


def parse_contest_date(value: str) -> str:
    value = (value or '').strip()
    try:
        parsed = datetime.strptime(value, '%d.%m.%Y')
    except ValueError as exc:
        raise ValidationError('Дата должна быть в формате ДД.ММ.ГГГГ. Например: 31.08.2026.') from exc
    if parsed.date() < datetime.now().date():
        raise ValidationError('Дата окончания конкурса не может быть в прошлом.')
    return value


def parse_report_date(value: str) -> str:
    value = (value or '').strip()
    try:
        parsed = datetime.strptime(value, '%d.%m.%Y')
    except ValueError as exc:
        raise ValidationError('Дата должна быть в формате ДД.ММ.ГГГГ. Например: 24.08.2026.') from exc
    if parsed.date() > datetime.now().date():
        raise ValidationError('Дата выполненной работы не может быть в будущем.')
    return value


def parse_points(value: str | int, *, allow_zero: bool = False, max_abs: int = 10000) -> int:
    try:
        amount = int(str(value).strip())
    except Exception as exc:
        raise ValidationError('Количество баллов должно быть целым числом. Например: +5 или -2.') from exc
    if amount == 0 and not allow_zero:
        raise ValidationError('Количество баллов не может быть равно нулю.')
    if abs(amount) > max_abs:
        raise ValidationError(f'Слишком большое значение. Максимум за одно действие: {max_abs}.')
    return amount


def parse_level(value: str | int) -> int:
    try:
        level = int(str(value).strip())
    except Exception as exc:
        raise ValidationError('Уровень должности должен быть числом от 1 до 99.') from exc
    if not 1 <= level <= 99:
        raise ValidationError('Уровень должности должен быть от 1 до 99. Уровень 100 зарезервирован владельцу.')
    return level


def normalize_vk_reference(raw: str) -> str | int:
    value = (raw or '').strip()
    if not value:
        raise ValidationError('Укажи VK ID, @короткое_имя или ссылку на профиль VK.')

    # Common VK mention: [id123|Имя Фамилия]
    m = re.fullmatch(r'\[id(\d+)\|[^\]]+\]', value, flags=re.I)
    if m:
        return int(m.group(1))

    if value.startswith('@'):
        value = value[1:].strip()

    if value.lower().startswith(('http://', 'https://', 'www.')):
        url = value if '://' in value else 'https://' + value
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix('www.')
        if host not in {'vk.com', 'vk.ru', 'm.vk.com', 'm.vk.ru'}:
            raise ValidationError('Нужна ссылка именно на VK, например https://vk.com/id123456789.')
        segment = parsed.path.strip('/').split('/')[0]
        if not segment:
            raise ValidationError('В ссылке VK не указан профиль пользователя.')
        value = segment

    low = value.lower()
    for prefix in ('club', 'public', 'event'):
        if low.startswith(prefix) and low[len(prefix):].isdigit():
            raise ValidationError('Ты указал сообщество VK. Нужна ссылка или ID обычного пользователя.')

    if low.startswith('id') and low[2:].isdigit():
        return int(low[2:])
    if value.isdigit():
        vk_id = int(value)
        if vk_id <= 0:
            raise ValidationError('VK ID должен быть положительным числом.')
        return vk_id
    if not _SCREEN_RE.fullmatch(value):
        raise ValidationError('Не удалось распознать профиль. Используй VK ID, @username или ссылку vk.com/username.')
    return value
