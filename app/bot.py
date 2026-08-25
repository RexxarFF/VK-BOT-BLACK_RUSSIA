from __future__ import annotations

import asyncio
import json
import shlex
from loguru import logger
from vkbottle import GroupEventType
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle.polling import BotPolling

from .config import settings
from .errors import AppError, PermissionDenied, error_reference, vk_error_message
from .keyboards import (
    back_staff,
    confirm_contest,
    confirm_finish,
    contest_panel,
    contest_settings_keyboard,
    contest_template_settings,
    helper_menu,
    leadership_main,
    member_card,
    members_list,
    permissions_keyboard,
    points_user,
    remove_member_confirm,
    report_cancel,
    report_chat_panel,
    report_preview,
    remove_user_confirm,
    role_picker,
    roles_panel,
    settings_panel,
    template_picker,
    user_card,
    users_list,
)
from .models import CONTEST_TEMPLATES, PERMISSION_CATALOG
from .services import AppService
from .storage.json_store import JsonStore
from .validators import clean_text, parse_contest_date, parse_level, parse_points, parse_report_date, validate_nickname
from .vk_utils import photo_attachment_string, resolve_vk_user, strip_group_mention

if not settings.token or settings.token == 'PASTE_YOUR_GROUP_TOKEN_HERE':
    raise RuntimeError('Укажите VK_GROUP_TOKEN в .env или переменных окружения Bothost.')

bot = Bot(token=settings.token, polling=BotPolling(group_id=settings.group_id or None))
bot.labeler.message_view.replace_mention = True
store = JsonStore(settings.data_dir)
svc = AppService(store, settings.owner_ids)

# Text-input wizards. Key: (vk_user_id, peer_id)
states: dict[tuple[int, int], dict] = {}
# Temporary permission checkbox selections. Key: (vk_user_id, peer_id, role)
permission_sessions: dict[tuple[int, int, str], set[str]] = {}
# Last bot-owned panel in each user's DM. Keeps slash commands from creating chat clutter.
dm_panels: dict[int, int] = {}
# Peers where an obsolete persistent keyboard from v1/v2 has already been cleared.
legacy_keyboard_cleared: set[int] = set()


def is_private_peer(peer_id: int) -> bool:
    return int(peer_id) < 2_000_000_000


def state_key(user_id: int, peer_id: int) -> tuple[int, int]:
    return int(user_id), int(peer_id)


def _event_value(event, name: str, default=None):
    if hasattr(event, name):
        value = getattr(event, name)
        if value is not None:
            return value
    obj = getattr(event, 'object', None)
    if obj is not None and hasattr(obj, name):
        value = getattr(obj, name)
        if value is not None:
            return value
    return default


def event_payload(event) -> dict:
    raw = _event_value(event, 'payload', {})
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or '{}')
    except Exception:
        return {}


async def display_name(vk_id: int) -> str:
    try:
        info = await bot.api.users.get(user_ids=[vk_id])
        if info:
            return f'{info[0].first_name} {info[0].last_name}'.strip()
    except Exception:
        pass
    return f'VK {vk_id}'


async def safe_delete_message(message: Message):
    """Best-effort cleanup. VK may refuse deletion depending on chat rights/client rules."""
    try:
        if getattr(message, 'id', 0):
            await bot.api.messages.delete(message_ids=[int(message.id)], delete_for_all=1)
    except Exception:
        pass


def schedule_delete_message(message: Message) -> None:
    """Delete user input in background so UI updates are not delayed by an extra VK API request."""
    try:
        asyncio.create_task(safe_delete_message(message))
    except RuntimeError:
        pass


def empty_persistent_keyboard() -> str:
    # A non-inline empty keyboard hides old permanent keyboards left by v1/v2.
    return json.dumps({'one_time': True, 'buttons': []}, ensure_ascii=False)


async def clear_legacy_keyboard(peer_id: int) -> None:
    """Remove obsolete bottom keyboards once per peer without leaving a service message behind."""
    peer_id = int(peer_id)
    if peer_id in legacy_keyboard_cleared:
        return
    legacy_keyboard_cleared.add(peer_id)
    try:
        mid = await bot.api.messages.send(
            peer_id=peer_id,
            message='·',
            keyboard=empty_persistent_keyboard(),
            random_id=0,
        )
        try:
            await bot.api.messages.delete(message_ids=[int(mid)], delete_for_all=1)
        except Exception:
            pass
    except Exception as exc:
        logger.debug('Не удалось убрать старую клавиатуру в peer {}: {}', peer_id, exc)


async def safe_edit(peer_id: int, *, text: str, keyboard: str | None = None, message_id: int | None = None,
                    cmid: int | None = None, attachment: str | None = None) -> bool:
    kwargs = {'peer_id': int(peer_id), 'message': text}
    if keyboard is not None:
        kwargs['keyboard'] = keyboard
    if attachment is not None:
        kwargs['attachment'] = attachment
    if message_id:
        kwargs['message_id'] = int(message_id)
    elif cmid:
        kwargs['conversation_message_id'] = int(cmid)
    else:
        return False
    try:
        await bot.api.messages.edit(**kwargs)
        return True
    except Exception as exc:
        logger.debug('Не удалось отредактировать сообщение: {}', exc)
        return False


async def send_or_edit_dm(user_id: int, text: str, keyboard: str | None = None, attachment: str | None = '') -> int:
    old = dm_panels.get(int(user_id))
    if old and await safe_edit(int(user_id), text=text, keyboard=keyboard, message_id=old, attachment=attachment):
        return old
    mid = await bot.api.messages.send(peer_id=int(user_id), message=text, keyboard=keyboard, attachment=attachment or None, random_id=0)
    dm_panels[int(user_id)] = int(mid)
    return int(mid)


async def edit_state_panel(user_id: int, peer_id: int, text: str, keyboard: str | None = None, attachment: str | None = None):
    st = states.get(state_key(user_id, peer_id), {})
    if await safe_edit(
        peer_id,
        text=text,
        keyboard=keyboard,
        message_id=st.get('panel_message_id'),
        cmid=st.get('panel_cmid'),
        attachment=attachment,
    ):
        return
    mid = await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=keyboard, attachment=attachment, random_id=0)
    st['panel_message_id'] = int(mid)
    st.pop('panel_cmid', None)
    states[state_key(user_id, peer_id)] = st
    if is_private_peer(peer_id):
        dm_panels[int(user_id)] = int(mid)


async def event_edit(event, text: str, keyboard: str | None = None, attachment: str | None = None):
    peer_id = int(_event_value(event, 'peer_id', 0) or 0)
    cmid = int(_event_value(event, 'conversation_message_id', 0) or 0)
    if await safe_edit(peer_id, text=text, keyboard=keyboard, cmid=cmid, attachment=attachment):
        return
    # Modern VKBottle provides this helper; use as fallback.
    try:
        kwargs = {'message': text}
        if keyboard is not None:
            kwargs['keyboard'] = keyboard
        if attachment is not None:
            kwargs['attachment'] = attachment
        await event.message_edit(**kwargs)
    except Exception:
        await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=keyboard, attachment=attachment, random_id=0)


async def snackbar(event, text: str):
    try:
        await event.show_snackbar(text[:90])
    except Exception:
        try:
            await bot.api.messages.send_message_event_answer(
                event_id=_event_value(event, 'event_id'),
                user_id=int(_event_value(event, 'user_id', 0)),
                peer_id=int(_event_value(event, 'peer_id', 0)),
                event_data=json.dumps({'type': 'show_snackbar', 'text': text[:90]}, ensure_ascii=False),
            )
        except Exception:
            pass


async def send_log(text: str):
    cfg = await svc.settings()
    peer = cfg.get('peers', {}).get('logs')
    if not peer:
        return
    try:
        await bot.api.messages.send(peer_id=int(peer), message=text, random_id=0)
    except Exception as exc:
        logger.warning('Не удалось отправить лог в VK: {}', exc)


async def user_facing_error(peer_id: int, exc: Exception, *, panel_message_id: int | None = None, panel_cmid: int | None = None):
    if isinstance(exc, AppError):
        text = f'❌ {exc.message}'
    elif isinstance(exc, PermissionError):
        text = '❌ У тебя недостаточно прав для этого действия.'
    elif exc.__class__.__name__.startswith('VKAPIError') or hasattr(exc, 'error_msg'):
        ref = error_reference()
        logger.exception('VK API error [{}]', ref)
        text = f'❌ {vk_error_message(exc)}\n\nКод ошибки: {ref}'
    else:
        ref = error_reference()
        logger.exception('Необработанная ошибка [{}]', ref)
        text = f'❌ Произошла внутренняя ошибка. Попробуй ещё раз.\n\nКод ошибки: {ref}'
    if panel_message_id or panel_cmid:
        if await safe_edit(peer_id, text=text, keyboard=back_staff(), message_id=panel_message_id, cmid=panel_cmid):
            return
    await bot.api.messages.send(peer_id=peer_id, message=text, random_id=0)


async def require_registered(vk_id: int) -> dict:
    user = await svc.get_user(vk_id)
    if vk_id in settings.owner_ids:
        return await svc.ensure_user(vk_id, await display_name(vk_id), registered=True)
    if not user or not user.get('registered') or not user.get('active'):
        raise PermissionDenied('Твой VK-аккаунт не зарегистрирован в системе Агентов Поддержки. Обратись к руководству.')
    return user


async def show_helper_menu(peer_id: int, user_id: int, *, message_id: int | None = None, cmid: int | None = None):
    user = await require_registered(user_id)
    name = user.get('nickname') or user.get('name') or await display_name(user_id)
    text = f'👋 Привет, {name}!\n\nВыбери нужный раздел:'
    kb = helper_menu(await svc.is_staff(user_id))
    if message_id or cmid:
        if await safe_edit(peer_id, text=text, keyboard=kb, message_id=message_id, cmid=cmid, attachment=''):
            if message_id and is_private_peer(peer_id):
                dm_panels[int(user_id)] = int(message_id)
            return
    if is_private_peer(peer_id):
        await send_or_edit_dm(user_id, text, kb)
    else:
        await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=kb, random_id=0)


async def profile_text(user_id: int) -> str:
    stats = await svc.profile_stats(user_id)
    user = stats['user']
    nickname = user.get('nickname') or 'не указан'
    lines = [
        '👤 ПРОФИЛЬ', '',
        f'🎮 Nick_Name: {nickname}',
        f'🎭 Должность: {user.get("role", "Агент Поддержки")}', '',
        '🏆 Результаты конкурсов:',
        f'🥇 Первых мест: {stats["first"]}',
        f'🥈 Вторых мест: {stats["second"]}',
        f'🥉 Третьих мест: {stats["third"]}',
        f'📚 Завершённых конкурсов: {stats["contests"]}',
    ]
    if stats['active_points'] is not None:
        lines += ['', '🔥 Активный конкурс:', f'⭐ Баллы: {stats["active_points"]}', f'📊 Место: #{stats["active_place"]}']
    return '\n'.join(lines)


async def ranking_text() -> str:
    contest, rows = await svc.ranking()
    if not contest:
        return 'ℹ️ Сейчас нет активного конкурса.'
    lines = [f'🏆 {contest["name"]}', f'📅 До: {contest["end_date"]}', '', '📊 Рейтинг:']
    if not rows:
        lines.append('Пока нет участников.')
    medals = {1: '🥇', 2: '🥈', 3: '🥉'}
    for row in rows[:30]:
        prefix = medals.get(row['place'], f'{row["place"]}.')
        lines.append(f'{prefix} {row["nickname"]} — {row["points"]}')
    return '\n'.join(lines)


async def leadership_home_text() -> str:
    contest = await svc.active_contest()
    _, members = await svc.participants()
    if contest:
        status = f'🏆 Активный конкурс: {contest["name"]}\n👥 Участников: {len(members)}'
    else:
        status = '🏆 Активного конкурса сейчас нет.'
    return f'👑 УПРАВЛЕНИЕ АП\n\n{status}\n\nВыбери раздел:'


async def contest_text() -> str:
    contest = await svc.active_contest()
    if not contest:
        return '🏆 КОНКУРС\n\nАктивного конкурса нет. Создай новый конкурс.'
    template = CONTEST_TEMPLATES.get(contest.get('template'), {}).get('name', contest.get('template'))
    return (
        f'🏆 КОНКУРС\n\n'
        f'Название: {contest["name"]}\n'
        f'Описание: {contest["description"]}\n'
        f'📅 До: {contest["end_date"]}\n'
        f'🧩 Шаблон: {template}\n'
        f'👥 Участников: {len(contest.get("members", {}))}'
    )


async def member_text(target: int) -> str:
    contest, rows = await svc.participants()
    row = next((x for x in rows if x['vk_id'] == int(target)), None)
    if not row:
        raise PermissionDenied('Этот Агент не участвует в активном конкурсе.')
    return (
        f'👤 {row["nickname"]}\n\n'
        f'VK: {row["vk_name"]}\n'
        f'🆔 {row["vk_id"]}\n'
        f'🎭 {row["role"]}\n'
        f'⭐ Баллы: {row["points"]}'
    )


async def user_text(target: int) -> tuple[str, dict]:
    data = await svc.user_card(target)
    lines = [
        f'👤 {data.get("nickname") or "Nick_Name не указан"}', '',
        f'VK: {data.get("name") or f"VK {target}"}',
        f'🆔 {target}',
        f'🎭 Должность: {data.get("role", "Агент Поддержки")}',
    ]
    if data.get('in_active_contest'):
        lines += ['', f'🏆 В конкурсе: {data.get("active_contest_name")}', f'⭐ Баллы: {data.get("active_points", 0)}']
    else:
        lines += ['', '🏆 В активном конкурсе не участвует.']
    return '\n'.join(lines), data


async def roles_text() -> str:
    roles = await svc.roles()
    lines = ['🎭 ДОЛЖНОСТИ', '']
    for name, role in sorted(roles.items(), key=lambda x: -int(x[1].get('level', 0))):
        perms = role.get('permissions', [])
        lines.append(f'• {name} — уровень {role.get("level", 0)}, прав: {"все" if "*" in perms else len(perms)}')
    return '\n'.join(lines)


async def logs_text(user_id: int) -> str:
    entries = await svc.audit_entries(user_id, 20)
    labels = {
        'user.register': 'Добавлен Агент',
        'user.register.from_contest': 'Агент зарегистрирован через конкурс',
        'user.deactivate': 'Агент исключён из системы',
        'contest.create': 'Создан конкурс',
        'contest.edit': 'Изменён конкурс',
        'contest.finish': 'Завершён конкурс',
        'contest.member.add': 'Участник добавлен в конкурс',
        'contest.member.remove': 'Участник исключён из конкурса',
        'points.change': 'Изменены баллы',
        'role.create': 'Создана должность',
        'role.assign': 'Назначена должность',
        'role.permissions': 'Изменены права должности',
        'report.submit': 'Подан отчёт',
        'settings.peer': 'Назначена беседа',
    }
    lines = ['📜 ПОСЛЕДНИЕ ДЕЙСТВИЯ', '']
    if not entries:
        lines.append('Пока действий нет.')
    for item in entries:
        action = labels.get(item.get('action'), item.get('action', 'Действие'))
        details = item.get('details', {})
        target = details.get('target')
        suffix = f' → id{target}' if target else ''
        lines.append(f'• {action}{suffix} — id{item.get("actor")}')
    return '\n'.join(lines)


async def settings_text() -> str:
    cfg = await svc.settings()
    peers = cfg.get('peers', {})
    return (
        '⚙️ НАСТРОЙКИ\n\n'
        f'📝 Беседа отчётов: {peers.get("reports") or "не назначена"}\n'
        f'👑 Беседа руководства: {peers.get("leadership") or "не назначена"}\n'
        f'📜 Беседа логов: {peers.get("logs") or "не назначена"}\n\n'
        'Назначение беседы:\n'
        '/setchat reports\n/setchat leadership\n/setchat logs'
    )


async def staff_panel_feedback(peer_id: int, text: str, keyboard: str | None = None):
    """Edit the shared leadership panel whenever possible instead of posting chat clutter."""
    cfg = await svc.settings()
    leadership = cfg.get('peers', {}).get('leadership')
    panel_id = cfg.get('panel_messages', {}).get('leadership')
    kb = keyboard or leadership_main()
    if leadership and int(leadership) == int(peer_id) and panel_id:
        if await safe_edit(peer_id, text=text, keyboard=kb, message_id=int(panel_id)):
            return
    await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=kb if is_private_peer(peer_id) else None, random_id=0)


async def clean_user_response(message: Message, text: str):
    """Keep chats clean: personal informational commands reuse one DM panel."""
    await send_or_edit_dm(int(message.from_id), text, helper_menu(await svc.is_staff(message.from_id)))


async def start_report_flow(user_id: int, peer_id: int, *, panel_cmid: int | None = None, panel_message_id: int | None = None):
    await require_registered(user_id)
    await svc.require(user_id, 'reports.submit')
    contest = await svc.active_contest()
    if not contest:
        raise PermissionDenied('Сейчас нет активного конкурса. Подача отчётов недоступна.')
    if str(user_id) not in contest.get('members', {}):
        raise PermissionDenied('Ты не добавлен в активный конкурс. Обратись к руководству АП.')
    if panel_message_id is None and panel_cmid is None and is_private_peer(peer_id):
        panel_message_id = dm_panels.get(int(user_id))
    st = {
        'kind': 'report', 'step': 1, 'data': {},
        'panel_cmid': panel_cmid, 'panel_message_id': panel_message_id,
    }
    states[state_key(user_id, peer_id)] = st
    text = '📝 ПОДАЧА ОТЧЁТА\n\nШаг 1/3\nУкажи свой Nick_Name:'
    if panel_cmid or panel_message_id:
        await edit_state_panel(user_id, peer_id, text, report_cancel(), attachment='')
    else:
        mid = await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=report_cancel(), random_id=0)
        st['panel_message_id'] = int(mid)
        if is_private_peer(peer_id):
            dm_panels[int(user_id)] = int(mid)


async def publish_report(user_id: int, peer_id: int, st: dict):
    cfg = await svc.settings()
    reports_peer = cfg.get('peers', {}).get('reports')
    if not reports_peer:
        raise PermissionDenied('Беседа отчётов ещё не назначена. Сообщи руководству, чтобы использовали /setchat reports.')
    data = st.get('data', {})
    report = await svc.create_report(user_id, data['nickname'], data['work_date'], data['proof'])
    message = (
        f'📝 ОТЧЁТ #{report["id"]}\n\n'
        f'👤 Nick_Name: {report["nickname"]}\n'
        f'📅 Дата выполненной работы: {report["work_date"]}\n\n'
        f'📸 Доказательства прикреплены ниже.'
    )
    await bot.api.messages.send(
        peer_id=int(reports_peer), message=message, attachment=report['proof_attachment'], random_id=0
    )
    states.pop(state_key(user_id, peer_id), None)
    await safe_edit(
        peer_id,
        text='✅ ОТЧЁТ ОТПРАВЛЕН\n\nОтчёт успешно опубликован в беседе отчётов.',
        keyboard=helper_menu(await svc.is_staff(user_id)),
        message_id=st.get('panel_message_id'),
        cmid=st.get('panel_cmid'),
        attachment='',
    )


async def process_state(message: Message) -> bool:
    key = state_key(message.from_id, message.peer_id)
    st = states.get(key)
    if not st:
        return False
    text = (message.text or '').strip()
    if text.lower() in {'/cancel', 'cancel', 'отмена'}:
        schedule_delete_message(message)
        states.pop(key, None)
        await show_helper_menu(message.peer_id, message.from_id, message_id=st.get('panel_message_id'), cmid=st.get('panel_cmid'))
        return True

    try:
        kind = st['kind']
        step = int(st.get('step', 1))
        data = st.setdefault('data', {})

        if kind == 'report':
            if step == 1:
                data['nickname'] = validate_nickname(text)
                st['step'] = 2
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '📝 ПОДАЧА ОТЧЁТА\n\nШаг 2/3\nУкажи дату выполненной работы в формате ДД.ММ.ГГГГ:', report_cancel())
                return True
            if step == 2:
                data['work_date'] = parse_report_date(text)
                st['step'] = 3
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '📝 ПОДАЧА ОТЧЁТА\n\nШаг 3/3\nПришли следующим сообщением скриншот доказательства как фотографию VK.', report_cancel())
                return True
            proof = photo_attachment_string(message)
            if not proof:
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '❌ Я не вижу фотографию.\n\nПришли скриншот именно как фото/изображение VK, а не ссылкой или обычным текстом.', report_cancel())
                return True
            data['proof'] = proof
            st['step'] = 4
            schedule_delete_message(message)
            preview = (
                '📋 ПРЕДПРОСМОТР ОТЧЁТА\n\n'
                f'👤 Nick_Name: {data["nickname"]}\n'
                f'📅 Дата выполненной работы: {data["work_date"]}\n\n'
                '📸 Доказательства прикреплены.\n\n'
                'Проверь данные перед отправкой.'
            )
            await edit_state_panel(message.from_id, message.peer_id, preview, report_preview(), attachment=proof)
            return True

        if kind == 'contest_create':
            if step == 1:
                data['name'] = clean_text(text, field='Название конкурса', min_len=3, max_len=80)
                st['step'] = 2
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '🏆 СОЗДАНИЕ КОНКУРСА\n\nШаг 2/4\nВведите описание конкурса:', report_cancel())
                return True
            if step == 2:
                data['description'] = clean_text(text, field='Описание конкурса', min_len=3, max_len=1200)
                st['step'] = 3
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '🏆 СОЗДАНИЕ КОНКУРСА\n\nШаг 3/4\nВведите дату окончания в формате ДД.ММ.ГГГГ:', report_cancel())
                return True
            if step == 3:
                data['end_date'] = parse_contest_date(text)
                st['step'] = 4
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '🏆 СОЗДАНИЕ КОНКУРСА\n\nШаг 4/4\nВыберите шаблон конкурса:', template_picker())
                return True

        if kind == 'contest_edit':
            field = data.get('field')
            await svc.edit_contest(message.from_id, field, text)
            schedule_delete_message(message)
            states.pop(key, None)
            await edit_state_panel(message.from_id, message.peer_id, f'✅ Настройка конкурса обновлена.\n\n{await contest_text()}', contest_settings_keyboard())
            return True

        if kind == 'user_add':
            if step == 1:
                target = await resolve_vk_user(bot.api, text)
                data['vk_id'] = target['vk_id']
                data['vk_name'] = target['name']
                st['step'] = 2
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, f'✅ Найден пользователь: {target["name"]}\n🆔 {target["vk_id"]}\n\nВведите его игровой Nick_Name:', report_cancel())
                return True
            nickname = validate_nickname(text)
            user = await svc.register_user(message.from_id, data['vk_id'], data['vk_name'], nickname)
            schedule_delete_message(message)
            states.pop(key, None)
            text_user, card_data = await user_text(data['vk_id'])
            await edit_state_panel(message.from_id, message.peer_id, f'✅ АГЕНТ ДОБАВЛЕН\n\n{text_user}', user_card(data['vk_id'], in_contest=card_data['in_active_contest']))
            await send_log(f'👤 В систему добавлен {nickname} (id{data["vk_id"]})\nДействие: id{message.from_id}')
            return True

        if kind == 'member_add':
            if step == 1:
                target = await resolve_vk_user(bot.api, text)
                data['vk_id'] = target['vk_id']
                data['vk_name'] = target['name']
                st['step'] = 2
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, f'✅ Найден пользователь: {target["name"]}\n🆔 {target["vk_id"]}\n\nВведите его игровой Nick_Name:', report_cancel())
                return True
            nickname = validate_nickname(text)
            user = await svc.add_member(message.from_id, data['vk_id'], data['vk_name'], nickname)
            schedule_delete_message(message)
            states.pop(key, None)
            await edit_state_panel(message.from_id, message.peer_id, f'✅ УЧАСТНИК ДОБАВЛЕН\n\n👤 {nickname}\nVK: {data["vk_name"]}\n🆔 {data["vk_id"]}', contest_panel(True))
            await send_log(f'👥 В конкурс добавлен {nickname} (id{data["vk_id"]})\nДействие: id{message.from_id}')
            return True

        if kind == 'points_custom':
            amount = parse_points(text, max_abs=settings.max_points_change)
            target = int(data['target'])
            before, after = await svc.change_points(message.from_id, target, amount)
            schedule_delete_message(message)
            states.pop(key, None)
            await edit_state_panel(message.from_id, message.peer_id, f'✅ Баллы изменены: {before} → {after} ({amount:+d})', points_user(target))
            await send_log(f'⭐ id{target}: {before} → {after} ({amount:+d})\nДействие: id{message.from_id}')
            return True

        if kind == 'role_create':
            if step == 1:
                data['name'] = clean_text(text, field='Название должности', min_len=2, max_len=50)
                st['step'] = 2
                schedule_delete_message(message)
                await edit_state_panel(message.from_id, message.peer_id, '🎭 СОЗДАНИЕ ДОЛЖНОСТИ\n\nВведите уровень должности от 1 до 10.\nЧем выше уровень — тем выше должность:', report_cancel())
                return True
            level = parse_level(text)
            await svc.create_role(message.from_id, data['name'], level)
            schedule_delete_message(message)
            states.pop(key, None)
            await edit_state_panel(message.from_id, message.peer_id, f'✅ Должность «{data["name"]}» создана.\n\nТеперь настрой ей права через кнопку «🔐 Настроить права» или /setperm.', roles_panel())
            return True

        if kind == 'role_assign':
            if step == 1:
                target = await resolve_vk_user(bot.api, text)
                schedule_delete_message(message)
                data['target'] = target['vk_id']
                data['target_name'] = target['name']
                roles = await svc.roles()
                st['step'] = 2
                await edit_state_panel(message.from_id, message.peer_id, f'🎭 НАЗНАЧЕНИЕ ДОЛЖНОСТИ\n\nПользователь: {target["name"]}\nВыберите должность:', role_picker(roles.keys(), 'role_assign_pick', target=target['vk_id']))
                return True

        # Some wizard stages wait for a callback button instead of text input.
        # Consume and remove any typed text here so it can NEVER fall through to
        # the slash-command parser and produce "Неизвестная команда" mid-form.
        schedule_delete_message(message)
        return True

    except Exception as exc:
        # Keep wizard alive on validation errors so the user can simply retry.
        if isinstance(exc, AppError):
            schedule_delete_message(message)
            await edit_state_panel(message.from_id, message.peer_id, f'❌ {exc.message}\n\nИсправь данные и попробуй ещё раз.', report_cancel())
            return True
        states.pop(key, None)
        await user_facing_error(message.peer_id, exc, panel_message_id=st.get('panel_message_id'), panel_cmid=st.get('panel_cmid'))
        return True
    # Active wizard input is always consumed.
    schedule_delete_message(message)
    return True


async def ensure_report_panel(peer_id: int) -> int:
    cfg = await svc.settings()
    old = cfg.get('panel_messages', {}).get('reports')
    text = '📝 ОТЧЁТЫ АГЕНТОВ ПОДДЕРЖКИ\n\nНажми кнопку ниже, чтобы подать отчёт. Заполнение пройдёт приватно в ЛС бота.'
    if old and await safe_edit(peer_id, text=text, keyboard=report_chat_panel(), message_id=int(old)):
        return int(old)
    mid = await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=report_chat_panel(), random_id=0)
    await svc.set_panel_message('reports', int(mid))
    return int(mid)


async def ensure_leadership_panel(peer_id: int) -> int:
    cfg = await svc.settings()
    old = cfg.get('panel_messages', {}).get('leadership')
    text = await leadership_home_text()
    if old and await safe_edit(peer_id, text=text, keyboard=leadership_main(), message_id=int(old)):
        return int(old)
    mid = await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=leadership_main(), random_id=0)
    await svc.set_panel_message('leadership', int(mid))
    return int(mid)



async def cleanup_configured_peer_keyboards() -> None:
    """Remove v1/v2 persistent keyboards from configured service chats on every deploy."""
    cfg = await svc.settings()
    peers = {int(p) for p in cfg.get('peers', {}).values() if p}
    if not peers:
        return
    await asyncio.gather(*(clear_legacy_keyboard(peer_id) for peer_id in peers), return_exceptions=True)


def split_vk_target(raw_command_text: str, command: str) -> tuple[str, str]:
    """Split `/cmd <VK reference> <rest>` and keep VK mentions with spaces intact."""
    rest = raw_command_text[len(command):].strip()
    if not rest:
        return '', ''
    if rest.startswith('['):
        end = rest.find(']')
        if end != -1:
            return rest[:end + 1], rest[end + 1:].strip()
    first, sep, tail = rest.partition(' ')
    return first, tail.strip() if sep else ''


async def handle_command(message: Message, text: str) -> bool:
    if not text.startswith('/'):
        return False
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    command = parts[0].lower()
    args = parts[1:]

    # Commands are control input, not chat history. Remove them whenever VK allows it.
    schedule_delete_message(message)

    if command in {'/start', '/menu'}:
        if not is_private_peer(message.peer_id):
            schedule_delete_message(message)
            return True
        schedule_delete_message(message)
        await show_helper_menu(message.peer_id, message.from_id)
        return True

    if command == '/help':
        await require_registered(message.from_id)
        staff = await svc.is_staff(message.from_id)
        text_help = (
            '📋 КОМАНДЫ\n\n'
            '/profile — профиль\n'
            '/top — рейтинг конкурса\n'
            '/report — подать отчёт\n'
            '/menu — главное меню'
        )
        if staff:
            text_help += (
                '\n\n👑 Руководство:\n'
                '/panel — панель управления\n'
                '/adduser <VK> <Nick_Name> — добавить АП в систему\n'
                '/addmember <VK> <Nick_Name> — добавить в активный конкурс\n'
                '/delmember <VK> — исключить из конкурса\n'
                '/points <VK> <+N|-N> [причина] — изменить баллы\n'
                '/newcontest — создать конкурс\n'
                '/finishcontest — завершить конкурс\n'
                '/addrole [название] — создать должность\n'
                '/setrole <VK> <должность> — назначить должность\n'
                '/setperm <должность> — настроить права\n'
                '/roles — список должностей\n'
                '/setchat <reports|leadership|logs> — назначить текущую беседу'
            )
        schedule_delete_message(message)
        await clean_user_response(message, text_help)
        return True

    if command == '/profile':
        await require_registered(message.from_id)
        schedule_delete_message(message)
        await clean_user_response(message, await profile_text(message.from_id))
        return True

    if command in {'/top', '/ranking'}:
        await require_registered(message.from_id)
        schedule_delete_message(message)
        await clean_user_response(message, await ranking_text())
        return True

    if command == '/report':
        schedule_delete_message(message)
        if not is_private_peer(message.peer_id):
            try:
                await start_report_flow(message.from_id, message.from_id)
            except Exception as exc:
                await user_facing_error(message.from_id, exc)
            return True
        await start_report_flow(message.from_id, message.peer_id)
        return True

    # Staff-only commands from here. Unknown/administrative commands from a helper
    # are answered privately so report chats stay clean.
    if not await svc.is_staff(message.from_id):
        await clean_user_response(message, '❌ Эта команда доступна только руководству. Используй /help для своих команд.')
        return True

    if command == '/panel':
        schedule_delete_message(message)
        cfg = await svc.settings()
        leadership = cfg.get('peers', {}).get('leadership')
        if is_private_peer(message.peer_id):
            await send_or_edit_dm(message.from_id, await leadership_home_text(), leadership_main())
        else:
            if leadership and int(leadership) != int(message.peer_id):
                raise PermissionDenied('Панель руководства доступна только в назначенной беседе руководства.')
            await ensure_leadership_panel(message.peer_id)
        return True

    if command == '/setchat':
        if not args:
            raise PermissionDenied('Укажи тип: /setchat reports, /setchat leadership или /setchat logs.')
        key = await svc.set_peer(message.from_id, args[0], message.peer_id)
        schedule_delete_message(message)
        if key == 'reports':
            await ensure_report_panel(message.peer_id)
        elif key == 'leadership':
            await ensure_leadership_panel(message.peer_id)
        else:
            await bot.api.messages.send(peer_id=message.peer_id, message='✅ Эта беседа назначена как беседа логов.', random_id=0)
        return True

    if command == '/adduser':
        ref, nickname = split_vk_target(text, command)
        if not ref or not nickname:
            raise PermissionDenied('Формат: /adduser <VK ID/ссылка/упоминание> <Nick_Name>')
        target = await resolve_vk_user(bot.api, ref)
        user = await svc.register_user(message.from_id, target['vk_id'], target['name'], nickname)
        await staff_panel_feedback(message.peer_id, f'✅ Агент добавлен в систему: {user["nickname"]} (id{target["vk_id"]}).')
        return True

    if command == '/addmember':
        ref, nickname = split_vk_target(text, command)
        if not ref or not nickname:
            raise PermissionDenied('Формат: /addmember <VK ID/ссылка/упоминание> <Nick_Name>')
        target = await resolve_vk_user(bot.api, ref)
        user = await svc.add_member(message.from_id, target['vk_id'], target['name'], nickname)
        await staff_panel_feedback(message.peer_id, f'✅ {user["nickname"]} добавлен в активный конкурс.', contest_panel(True))
        await send_log(f'👥 {user["nickname"]} добавлен в конкурс\nДействие: id{message.from_id}')
        return True

    if command == '/delmember':
        ref, _ = split_vk_target(text, command)
        if not ref:
            raise PermissionDenied('Формат: /delmember <VK ID/ссылка/упоминание>')
        target = await resolve_vk_user(bot.api, ref)
        await svc.remove_member(message.from_id, target['vk_id'])
        await staff_panel_feedback(message.peer_id, f'✅ {target["name"]} исключён из активного конкурса.')
        return True

    if command == '/points':
        ref, remainder = split_vk_target(text, command)
        p = remainder.split(maxsplit=1)
        if not ref or not p:
            raise PermissionDenied('Формат: /points <VK ID/ссылка/упоминание> <+N|-N> [причина]')
        target = await resolve_vk_user(bot.api, ref)
        amount = parse_points(p[0], max_abs=settings.max_points_change)
        reason = p[1].strip() if len(p) > 1 else 'Изменение командой /points'
        before, after = await svc.change_points(message.from_id, target['vk_id'], amount, reason)
        await staff_panel_feedback(message.peer_id, f'✅ {target["name"]}: {before} → {after} ({amount:+d}).')
        await send_log(f'⭐ id{target["vk_id"]}: {before} → {after} ({amount:+d})\nПричина: {reason}\nДействие: id{message.from_id}')
        return True

    if command == '/newcontest':
        await svc.require(message.from_id, 'contest.create')
        schedule_delete_message(message)
        mid = await ensure_leadership_panel(message.peer_id) if not is_private_peer(message.peer_id) else None
        states[state_key(message.from_id, message.peer_id)] = {'kind': 'contest_create', 'step': 1, 'data': {}, 'panel_message_id': mid}
        await edit_state_panel(message.from_id, message.peer_id, '🏆 СОЗДАНИЕ КОНКУРСА\n\nШаг 1/4\nВведите название конкурса:', report_cancel())
        return True

    if command == '/finishcontest':
        ranking = await svc.finish_contest(message.from_id)
        lines = ['🏁 Конкурс завершён.', '']
        for row in ranking[:10]:
            lines.append(f'{row["place"]}. {row["nickname"]} — {row["points"]}')
        await staff_panel_feedback(message.peer_id, '\n'.join(lines), contest_panel(False))
        return True

    if command == '/addrole':
        await svc.require(message.from_id, 'roles.create')
        name = ' '.join(args).strip()
        cfg = await svc.settings()
        leadership = cfg.get('peers', {}).get('leadership')
        panel_id = cfg.get('panel_messages', {}).get('leadership') if leadership and int(leadership) == int(message.peer_id) else None
        states[state_key(message.from_id, message.peer_id)] = {
            'kind': 'role_create', 'step': 2 if name else 1,
            'data': {'name': name} if name else {}, 'panel_message_id': panel_id,
        }
        if name:
            await edit_state_panel(message.from_id, message.peer_id, f'🎭 СОЗДАНИЕ ДОЛЖНОСТИ\n\nНазвание: {name}\nВведите уровень должности от 1 до 10:', report_cancel())
        else:
            await edit_state_panel(message.from_id, message.peer_id, '🎭 СОЗДАНИЕ ДОЛЖНОСТИ\n\nВведите название новой должности:', report_cancel())
        return True

    if command == '/setrole':
        ref, role_name = split_vk_target(text, command)
        if not ref or not role_name:
            raise PermissionDenied('Формат: /setrole <VK ID/ссылка/упоминание> <название должности>')
        target = await resolve_vk_user(bot.api, ref)
        await svc.assign_role(message.from_id, target['vk_id'], role_name)
        await staff_panel_feedback(message.peer_id, f'✅ Пользователю {target["name"]} назначена должность «{role_name}».', roles_panel())
        return True

    if command == '/roles':
        await svc.require(message.from_id, 'roles.view')
        await staff_panel_feedback(message.peer_id, await roles_text(), roles_panel())
        return True

    if command == '/setperm':
        await svc.require(message.from_id, 'roles.permissions')
        if not args:
            raise PermissionDenied('Формат: /setperm <название должности>')
        role_name = await svc.resolve_role_name(' '.join(args))
        roles = await svc.roles()
        selected = set(roles[role_name].get('permissions', []))
        permission_sessions[(message.from_id, message.peer_id, role_name)] = selected
        text_perm = f'🔐 ПРАВА: {role_name}\n\nНажимай на права, чтобы включать/выключать их. Потом нажми «💾 Сохранить».'
        cfg = await svc.settings()
        panel_id = cfg.get('panel_messages', {}).get('leadership')
        leadership = cfg.get('peers', {}).get('leadership')
        if leadership and int(leadership) == int(message.peer_id) and panel_id:
            if await safe_edit(message.peer_id, text=text_perm, keyboard=permissions_keyboard(role_name, PERMISSION_CATALOG, selected), message_id=int(panel_id)):
                return True
        await bot.api.messages.send(peer_id=message.peer_id, message=text_perm, keyboard=permissions_keyboard(role_name, PERMISSION_CATALOG, selected), random_id=0)
        return True

    # Unknown commands never overwrite the shared leadership panel and never
    # clutter a chat. The hint goes only to the sender's DM.
    await clean_user_response(message, '❌ Неизвестная команда. Используй /help.')
    return True


@bot.on.message()
async def message_handler(message: Message):
    try:
        # Old v1/v2 permanent keyboards can remain cached by VK clients even after
        # deploying new code. Clear them on the first interaction in every peer.
        asyncio.create_task(clear_legacy_keyboard(message.peer_id))
        text = strip_group_mention((message.text or '').strip())
        # Wizard input has absolute priority over command parsing.
        if await process_state(message):
            return
        if await handle_command(message, text):
            return

        # Text fallback for users who type button labels manually in DMs.
        if is_private_peer(message.peer_id):
            low = text.lower()
            if low in {'начать', 'старт', 'меню'}:
                schedule_delete_message(message)
                await show_helper_menu(message.peer_id, message.from_id)
            elif low == '📝 подать отчёт':
                schedule_delete_message(message)
                await start_report_flow(message.from_id, message.peer_id)
            # Unknown text in DM is ignored to avoid spam.
        # Unknown chat messages are always ignored.
    except Exception as exc:
        try:
            cfg = await svc.settings()
            leadership = cfg.get('peers', {}).get('leadership')
            panel_id = cfg.get('panel_messages', {}).get('leadership') if leadership and int(leadership) == int(message.peer_id) else None
            await user_facing_error(message.peer_id, exc, panel_message_id=int(panel_id) if panel_id else None)
        except Exception:
            logger.exception('Не удалось показать пользователю ошибку')


@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def callback_handler(event: MessageEvent):
    user_id = int(_event_value(event, 'user_id', 0) or 0)
    peer_id = int(_event_value(event, 'peer_id', 0) or 0)
    cmid = int(_event_value(event, 'conversation_message_id', 0) or 0)
    payload = event_payload(event)
    action = str(payload.get('action') or '')
    try:
        if action == 'profile':
            await require_registered(user_id)
            await event_edit(event, await profile_text(user_id), helper_menu(await svc.is_staff(user_id)))
            return
        if action == 'ranking':
            await require_registered(user_id)
            await event_edit(event, await ranking_text(), helper_menu(await svc.is_staff(user_id)))
            return
        if action == 'report_start':
            if not is_private_peer(peer_id):
                await snackbar(event, 'Открой личные сообщения бота для подачи отчёта.')
                return
            await start_report_flow(user_id, peer_id, panel_cmid=cmid)
            return
        if action == 'report_from_chat':
            await require_registered(user_id)
            try:
                await start_report_flow(user_id, user_id)
                await snackbar(event, 'Форма отчёта отправлена тебе в ЛС.')
            except Exception as exc:
                states.pop(state_key(user_id, user_id), None)
                code = getattr(exc, 'code', None)
                if code in (901, 902):
                    await snackbar(event, 'Сначала открой ЛС сообщества и нажми «Начать».')
                elif isinstance(exc, AppError):
                    await snackbar(event, exc.message)
                else:
                    raise
            return
        if action == 'report_confirm':
            st = states.get(state_key(user_id, peer_id))
            if not st or st.get('kind') != 'report' or st.get('step') != 4:
                await snackbar(event, 'Форма устарела. Начни подачу отчёта заново.')
                return
            await publish_report(user_id, peer_id, st)
            await snackbar(event, 'Отчёт отправлен!')
            return
        if action == 'report_restart':
            await start_report_flow(user_id, peer_id, panel_cmid=cmid)
            return
        if action == 'flow_cancel':
            if not is_private_peer(peer_id) and not await svc.is_staff(user_id):
                raise PermissionDenied()
            states.pop(state_key(user_id, peer_id), None)
            if is_private_peer(peer_id):
                await event_edit(event, '❌ Действие отменено.\n\nВыбери нужный раздел:', helper_menu(await svc.is_staff(user_id)), attachment='')
            else:
                await event_edit(event, await leadership_home_text(), leadership_main())
            return

        # Staff actions.
        if action.startswith(('staff_', 'contest_', 'member_', 'user_', 'points_', 'role_', 'perm_', 'roles_')):
            if not await svc.is_staff(user_id):
                raise PermissionDenied()

        if action in {'staff_hint', 'staff_home'}:
            await event_edit(event, await leadership_home_text(), leadership_main())
            return

        if action == 'staff_contest':
            contest = await svc.active_contest()
            await event_edit(event, await contest_text(), contest_panel(bool(contest)))
            return

        if action == 'contest_settings':
            await svc.require(user_id, 'contest.edit')
            if not await svc.active_contest():
                await snackbar(event, 'Сейчас нет активного конкурса.')
                return
            await event_edit(event, f'⚙️ НАСТРОЙКА КОНКУРСА\n\n{await contest_text()}\n\nЧто изменить?', contest_settings_keyboard())
            return

        if action in {'contest_edit_name', 'contest_edit_description', 'contest_edit_end_date'}:
            await svc.require(user_id, 'contest.edit')
            field_map = {
                'contest_edit_name': ('name', 'Введите новое название конкурса:'),
                'contest_edit_description': ('description', 'Введите новое описание конкурса:'),
                'contest_edit_end_date': ('end_date', 'Введите новую дату окончания в формате ДД.ММ.ГГГГ:'),
            }
            field, prompt = field_map[action]
            states[state_key(user_id, peer_id)] = {'kind': 'contest_edit', 'step': 1, 'data': {'field': field}, 'panel_cmid': cmid}
            await event_edit(event, f'⚙️ НАСТРОЙКА КОНКУРСА\n\n{prompt}', report_cancel())
            return

        if action == 'contest_edit_template':
            await svc.require(user_id, 'contest.edit')
            await event_edit(event, '🧩 Выберите шаблон конкурса:', contest_template_settings())
            return

        if action == 'contest_set_template':
            await svc.require(user_id, 'contest.edit')
            template = str(payload.get('template') or '')
            await svc.edit_contest(user_id, 'template', template)
            await event_edit(event, f'✅ Шаблон конкурса обновлён.\n\n{await contest_text()}', contest_settings_keyboard())
            return

        if action == 'contest_create':
            await svc.require(user_id, 'contest.create')
            states[state_key(user_id, peer_id)] = {'kind': 'contest_create', 'step': 1, 'data': {}, 'panel_cmid': cmid}
            await event_edit(event, '🏆 СОЗДАНИЕ КОНКУРСА\n\nШаг 1/4\nВведите название конкурса:', report_cancel())
            return

        if action == 'contest_template':
            st = states.get(state_key(user_id, peer_id))
            if not st or st.get('kind') != 'contest_create' or st.get('step') != 4:
                await snackbar(event, 'Мастер создания конкурса уже закрыт.')
                return
            template = str(payload.get('template') or '')
            if template not in CONTEST_TEMPLATES:
                await snackbar(event, 'Неизвестный шаблон конкурса.')
                return
            st['data']['template'] = template
            d = st['data']
            await event_edit(
                event,
                '🏆 ПРОВЕРЬ КОНКУРС\n\n'
                f'Название: {d["name"]}\n'
                f'Описание: {d["description"]}\n'
                f'Дата окончания: {d["end_date"]}\n'
                f'Шаблон: {CONTEST_TEMPLATES[template]["name"]}\n\n'
                'Создать конкурс?',
                confirm_contest(),
            )
            return

        if action == 'contest_confirm':
            st = states.get(state_key(user_id, peer_id))
            if not st or st.get('kind') != 'contest_create':
                await snackbar(event, 'Мастер создания конкурса уже закрыт.')
                return
            d = st['data']
            cid = await svc.create_contest(user_id, d['name'], d['description'], d['end_date'], d['template'])
            states.pop(state_key(user_id, peer_id), None)
            await event_edit(event, f'✅ Конкурс #{cid} «{d["name"]}» создан.\n\nТеперь добавь участников.', contest_panel(True))
            await send_log(f'🏆 Создан конкурс #{cid} «{d["name"]}»\nСоздал: id{user_id}')
            return

        if action == 'contest_finish_confirm':
            await svc.require(user_id, 'contest.finish')
            await event_edit(event, '⚠️ Завершить активный конкурс?\n\nПосле завершения итоговые места попадут в статистику профилей.', confirm_finish())
            return

        if action == 'contest_finish':
            ranking = await svc.finish_contest(user_id)
            lines = ['🏁 КОНКУРС ЗАВЕРШЁН', '']
            for row in ranking[:10]:
                lines.append(f'{row["place"]}. {row["nickname"]} — {row["points"]}')
            lines += ['', 'Результаты сохранены в профилях участников.']
            await event_edit(event, '\n'.join(lines), contest_panel(False))
            await send_log(f'🏁 Конкурс завершён\nДействие: id{user_id}')
            return

        if action == 'staff_users':
            await svc.require(user_id, 'users.view')
            rows = await svc.active_users()
            page = int(payload.get('page', 0) or 0)
            await event_edit(event, f'👥 АГЕНТЫ ПОДДЕРЖКИ\n\nВсего зарегистрировано: {len(rows)}\nВыбери нужного Агента:', users_list(rows, page))
            return

        if action == 'user_add':
            await svc.require(user_id, 'users.add')
            states[state_key(user_id, peer_id)] = {'kind': 'user_add', 'step': 1, 'data': {}, 'panel_cmid': cmid}
            await event_edit(event, '➕ ДОБАВЛЕНИЕ АГЕНТА\n\nШаг 1/2\nОтправьте VK ID, @username, упоминание или ссылку на страницу VK:', report_cancel())
            return

        if action == 'user_open':
            await svc.require(user_id, 'users.view')
            target = int(payload.get('target', 0) or 0)
            text_user, data = await user_text(target)
            await event_edit(event, text_user, user_card(target, in_contest=data['in_active_contest']))
            return

        if action == 'user_add_to_contest':
            await svc.require(user_id, 'contest.members.add')
            target = int(payload.get('target', 0) or 0)
            await svc.add_registered_user_to_contest(user_id, target)
            text_user, data = await user_text(target)
            await event_edit(event, f'✅ Агент добавлен в активный конкурс.\n\n{text_user}', user_card(target, in_contest=data['in_active_contest']))
            await send_log(f'👥 id{target} добавлен в конкурс\nДействие: id{user_id}')
            return

        if action == 'user_remove_confirm':
            await svc.require(user_id, 'users.remove')
            target = int(payload.get('target', 0) or 0)
            text_user, _ = await user_text(target)
            await event_edit(event, f'⚠️ Исключить Агента из системы?\n\n{text_user}\n\nИстория конкурсов сохранится, но доступ к боту будет закрыт.', remove_user_confirm(target))
            return

        if action == 'user_remove':
            await svc.require(user_id, 'users.remove')
            target = int(payload.get('target', 0) or 0)
            await svc.deactivate_user(user_id, target)
            rows = await svc.active_users()
            await event_edit(event, '✅ Агент исключён из системы. Его история сохранена.\n\nВыбери следующего Агента:', users_list(rows))
            await send_log(f'🚫 id{target} исключён из системы\nДействие: id{user_id}')
            return

        if action == 'staff_members':
            await svc.require(user_id, 'users.view')
            contest, rows = await svc.participants()
            if not contest:
                await event_edit(event, '👥 УЧАСТНИКИ\n\nСейчас нет активного конкурса.', back_staff())
                return
            page = int(payload.get('page', 0) or 0)
            await event_edit(event, f'👥 УЧАСТНИКИ КОНКУРСА\n\nВсего: {len(rows)}\nВыбери нужного Агента:', members_list(rows, page))
            return

        if action == 'member_add':
            await svc.require(user_id, 'contest.members.add')
            states[state_key(user_id, peer_id)] = {'kind': 'member_add', 'step': 1, 'data': {}, 'panel_cmid': cmid}
            await event_edit(event, '➕ ДОБАВЛЕНИЕ УЧАСТНИКА\n\nШаг 1/2\nОтправьте VK ID, @username, упоминание или ссылку на страницу VK:', report_cancel())
            return

        if action == 'member_open':
            target = int(payload.get('target', 0) or 0)
            await event_edit(event, await member_text(target), member_card(target))
            return

        if action == 'member_remove_confirm':
            target = int(payload.get('target', 0) or 0)
            await svc.require(user_id, 'contest.members.remove')
            await event_edit(event, f'⚠️ Исключить участника из активного конкурса?\n\n{await member_text(target)}', remove_member_confirm(target))
            return

        if action == 'member_remove':
            target = int(payload.get('target', 0) or 0)
            await svc.remove_member(user_id, target)
            contest, rows = await svc.participants()
            await event_edit(event, '✅ Участник исключён из активного конкурса.\n\nВыбери следующего участника:', members_list(rows))
            return

        if action == 'staff_points':
            await svc.require(user_id, 'users.view')
            contest, rows = await svc.participants()
            if not contest:
                await event_edit(event, '⭐ БАЛЛЫ\n\nСейчас нет активного конкурса.', back_staff())
                return
            page = int(payload.get('page', 0) or 0)
            await event_edit(event, f'⭐ УПРАВЛЕНИЕ БАЛЛАМИ\n\nВыберите участника:', members_list(rows, page, action='points_user', page_action='staff_points'))
            return

        if action == 'points_user':
            target = int(payload.get('target', 0) or 0)
            await event_edit(event, f'⭐ УПРАВЛЕНИЕ БАЛЛАМИ\n\n{await member_text(target)}\n\nВыбери изменение:', points_user(target))
            return

        if action == 'points_quick':
            target = int(payload.get('target', 0) or 0)
            amount = int(payload.get('amount', 0) or 0)
            before, after = await svc.change_points(user_id, target, amount)
            await event_edit(event, f'✅ Баллы изменены: {before} → {after} ({amount:+d})\n\n{await member_text(target)}', points_user(target))
            await snackbar(event, f'{amount:+d} баллов')
            await send_log(f'⭐ id{target}: {before} → {after} ({amount:+d})\nДействие: id{user_id}')
            return

        if action == 'points_custom':
            target = int(payload.get('target', 0) or 0)
            states[state_key(user_id, peer_id)] = {'kind': 'points_custom', 'step': 1, 'data': {'target': target}, 'panel_cmid': cmid}
            await event_edit(event, f'⭐ ДРУГОЕ ЗНАЧЕНИЕ\n\n{await member_text(target)}\n\nВведите число со знаком, например +7 или -3:', report_cancel())
            return

        if action == 'staff_ranking':
            await event_edit(event, await ranking_text(), back_staff())
            return

        if action == 'staff_roles':
            await svc.require(user_id, 'roles.view')
            await event_edit(event, '🎭 УПРАВЛЕНИЕ ДОЛЖНОСТЯМИ\n\nСоздавай должности, назначай их и настраивай права.', roles_panel())
            return

        if action == 'roles_list':
            await svc.require(user_id, 'roles.view')
            await event_edit(event, await roles_text(), roles_panel())
            return

        if action == 'role_create':
            await svc.require(user_id, 'roles.create')
            states[state_key(user_id, peer_id)] = {'kind': 'role_create', 'step': 1, 'data': {}, 'panel_cmid': cmid}
            await event_edit(event, '🎭 СОЗДАНИЕ ДОЛЖНОСТИ\n\nВведите название новой должности:', report_cancel())
            return

        if action == 'role_assign_start':
            await svc.require(user_id, 'roles.assign')
            states[state_key(user_id, peer_id)] = {'kind': 'role_assign', 'step': 1, 'data': {}, 'panel_cmid': cmid}
            await event_edit(event, '🎭 НАЗНАЧЕНИЕ ДОЛЖНОСТИ\n\nОтправьте VK ID, ссылку, @username или упоминание пользователя:', report_cancel())
            return

        if action == 'role_assign_user':
            target = int(payload.get('target', 0) or 0)
            roles = await svc.roles()
            await event_edit(event, '🎭 Выберите должность:', role_picker(roles.keys(), 'role_assign_pick', target=target))
            return

        if action == 'role_assign_pick':
            target = int(payload.get('target', 0) or 0)
            role_name = str(payload.get('role') or '')
            await svc.assign_role(user_id, target, role_name)
            states.pop(state_key(user_id, peer_id), None)
            await event_edit(event, f'✅ Пользователю id{target} назначена должность «{role_name}».', roles_panel())
            return

        if action == 'role_permissions_start':
            await svc.require(user_id, 'roles.permissions')
            roles = await svc.roles()
            await event_edit(event, '🔐 Выберите должность, права которой нужно настроить:', role_picker(roles.keys(), 'perm_role_pick'))
            return

        if action == 'perm_role_pick':
            role_name = str(payload.get('role') or '')
            roles = await svc.roles()
            if role_name not in roles:
                await snackbar(event, 'Должность не найдена.')
                return
            selected = set(roles[role_name].get('permissions', []))
            if '*' in selected:
                await snackbar(event, 'Права владельца менять нельзя.')
                return
            permission_sessions[(user_id, peer_id, role_name)] = selected
            await event_edit(event, f'🔐 ПРАВА: {role_name}\n\n✅ — право включено. Нажми на строку, чтобы переключить.', permissions_keyboard(role_name, PERMISSION_CATALOG, selected))
            return

        if action == 'perm_toggle':
            role_name = str(payload.get('role') or '')
            perm = str(payload.get('perm') or '')
            page = int(payload.get('page', 0) or 0)
            key = (user_id, peer_id, role_name)
            if key not in permission_sessions:
                roles = await svc.roles()
                permission_sessions[key] = set(roles.get(role_name, {}).get('permissions', []))
            selected = permission_sessions[key]
            if perm in selected:
                selected.remove(perm)
            else:
                selected.add(perm)
            await event_edit(event, f'🔐 ПРАВА: {role_name}\n\nВыбери нужные права и сохрани.', permissions_keyboard(role_name, PERMISSION_CATALOG, selected, page))
            return

        if action == 'perm_page':
            role_name = str(payload.get('role') or '')
            page = int(payload.get('page', 0) or 0)
            roles = await svc.roles()
            selected = permission_sessions.setdefault((user_id, peer_id, role_name), set(roles.get(role_name, {}).get('permissions', [])))
            await event_edit(event, f'🔐 ПРАВА: {role_name}\n\nВыбери нужные права и сохрани.', permissions_keyboard(role_name, PERMISSION_CATALOG, selected, page))
            return

        if action == 'perm_save':
            role_name = str(payload.get('role') or '')
            key = (user_id, peer_id, role_name)
            selected = permission_sessions.get(key, set())
            await svc.set_role_permissions(user_id, role_name, sorted(selected))
            permission_sessions.pop(key, None)
            await event_edit(event, f'✅ Права должности «{role_name}» сохранены.', roles_panel())
            return

        if action == 'role_picker_page':
            mode = str(payload.get('mode') or '')
            page = int(payload.get('page', 0) or 0)
            target = int(payload.get('target', 0) or 0) or None
            roles = await svc.roles()
            await event_edit(event, '🎭 Выберите должность:', role_picker(roles.keys(), mode, target=target, page=page))
            return

        if action == 'staff_logs':
            await svc.require(user_id, 'logs.view')
            await event_edit(event, await logs_text(user_id), back_staff())
            return

        if action == 'staff_settings':
            await svc.require(user_id, 'settings.manage')
            await event_edit(event, await settings_text(), settings_panel())
            return

        await snackbar(event, 'Эта кнопка устарела. Открой панель заново через /panel.')

    except Exception as exc:
        if isinstance(exc, AppError):
            await snackbar(event, exc.message)
        else:
            ref = error_reference()
            logger.exception('Ошибка callback [{}]', ref)
            await snackbar(event, f'Произошла ошибка. Код: {ref}')
