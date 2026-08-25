from __future__ import annotations

import re
from typing import Any

from .errors import NotFoundError, ValidationError
from .validators import normalize_vk_reference


def _mapping(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, 'dict'):
        return obj.dict()
    result = {}
    for key in ('id', 'owner_id', 'access_key', 'first_name', 'last_name', 'deactivated', 'screen_name', 'type', 'photo'):
        if hasattr(obj, key):
            result[key] = getattr(obj, key)
    return result


async def resolve_vk_user(api, raw: str | int) -> dict:
    reference = normalize_vk_reference(str(raw))
    try:
        users = await api.users.get(user_ids=[reference])
    except Exception as exc:
        code = getattr(exc, 'code', None)
        if code in (100, 113):
            raise ValidationError('Профиль VK не найден. Проверь ID или ссылку и попробуй ещё раз.') from exc
        raise
    if not users:
        raise NotFoundError('Пользователь VK не найден. Проверь ID или ссылку.', code='vk_user_not_found')
    user = _mapping(users[0])
    vk_id = int(user.get('id') or 0)
    if vk_id <= 0:
        raise NotFoundError('Не удалось определить VK ID пользователя.', code='vk_user_not_found')
    if user.get('deactivated'):
        raise ValidationError('Этот профиль VK удалён или заблокирован.')
    first = str(user.get('first_name') or '').strip()
    last = str(user.get('last_name') or '').strip()
    return {
        'vk_id': vk_id,
        'name': f'{first} {last}'.strip() or f'VK {vk_id}',
        'screen_name': user.get('screen_name'),
    }


def strip_group_mention(text: str) -> str:
    """Allow commands like [club123|Bot] /panel and @club123 /panel."""
    value = (text or '').strip()
    value = re.sub(r'^\[club\d+\|[^\]]+\]\s*', '', value, flags=re.I)
    value = re.sub(r'^@club\d+\s*', '', value, flags=re.I)
    return value.strip()


def photo_attachment_string(message: Any) -> str | None:
    """Extract first VK photo attachment from VKBottle Message in a version-tolerant way."""
    # VKBottle 4.x has a native helper that already builds valid attachment strings.
    try:
        strings = message.get_attachment_strings() or []
        for value in strings:
            if str(value).startswith('photo'):
                return str(value)
    except Exception:
        pass

    attachments = getattr(message, 'attachments', None) or []
    for item in attachments:
        data = _mapping(item)
        kind = data.get('type')
        photo = data.get('photo')
        if photo is None and hasattr(item, 'photo'):
            photo = getattr(item, 'photo')
        if kind not in (None, 'photo') and str(kind).lower() != 'photo':
            continue
        if photo is None:
            continue
        p = _mapping(photo)
        owner_id = p.get('owner_id')
        media_id = p.get('id')
        if owner_id is None or media_id is None:
            continue
        access_key = p.get('access_key')
        result = f'photo{owner_id}_{media_id}'
        if access_key:
            result += f'_{access_key}'
        return result
    return None
