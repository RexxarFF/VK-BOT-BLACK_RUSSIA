from __future__ import annotations

import json
from typing import Iterable


def cb(label: str, action: str, *, color: str = 'secondary', **payload) -> dict:
    data = {'action': action, **payload}
    return {
        'action': {
            'type': 'callback',
            'label': label[:40],
            'payload': json.dumps(data, ensure_ascii=False),
        },
        'color': color,
    }


def inline(rows: list[list[dict]]) -> str:
    return json.dumps({'inline': True, 'buttons': rows}, ensure_ascii=False)


def helper_menu(is_staff: bool = False) -> str:
    rows = [
        [cb('👤 Профиль', 'profile', color='primary'), cb('📊 Рейтинг конкурса', 'ranking', color='positive')],
        [cb('📝 Подать отчёт', 'report_start', color='positive')],
    ]
    if is_staff:
        rows.extend([
            [cb('🏆 Конкурс', 'staff_contest', color='primary'), cb('👥 Агенты', 'staff_users')],
            [cb('⭐ Баллы', 'staff_points', color='positive'), cb('🎭 Должности', 'staff_roles')],
            [cb('📜 Логи', 'staff_logs'), cb('⚙️ Настройки', 'staff_settings')],
        ])
    return inline(rows)


def report_chat_panel() -> str:
    return inline([[cb('📝 Подать отчёт', 'report_from_chat', color='positive')]])


def report_cancel() -> str:
    return inline([[cb('❌ Отмена', 'flow_cancel', color='negative')]])


def report_preview() -> str:
    return inline([
        [cb('✅ Отправить', 'report_confirm', color='positive')],
        [cb('🔄 Заполнить заново', 'report_restart'), cb('❌ Отмена', 'flow_cancel', color='negative')],
    ])


def leadership_main() -> str:
    return inline([
        [cb('🏆 Конкурс', 'staff_contest', color='primary'), cb('👥 Агенты', 'staff_users')],
        [cb('⭐ Баллы', 'staff_points', color='positive'), cb('🎭 Должности', 'staff_roles')],
        [cb('📊 Рейтинг', 'staff_ranking'), cb('📜 Логи', 'staff_logs')],
        [cb('⚙️ Настройки', 'staff_settings')],
    ])


def back_staff() -> str:
    return inline([[cb('⬅️ Назад', 'staff_home')]])


def contest_panel(has_active: bool) -> str:
    if not has_active:
        return inline([
            [cb('➕ Создать конкурс', 'contest_create', color='positive')],
            [cb('⬅️ Назад', 'staff_home')],
        ])
    return inline([
        [cb('⚙️ Настроить', 'contest_settings', color='primary'), cb('📊 Рейтинг', 'staff_ranking')],
        [cb('👥 Участники конкурса', 'staff_members')],
        [cb('➕ Добавить участника', 'member_add', color='positive')],
        [cb('🏁 Завершить конкурс', 'contest_finish_confirm', color='negative')],
        [cb('⬅️ Назад', 'staff_home')],
    ])


def template_picker() -> str:
    return inline([
        [cb('⭐ По конкурсным баллам', 'contest_template', color='primary', template='points')],
        [cb('❌ Отмена', 'flow_cancel', color='negative')],
    ])


def confirm_contest() -> str:
    return inline([
        [cb('✅ Создать', 'contest_confirm', color='positive')],
        [cb('❌ Отмена', 'flow_cancel', color='negative')],
    ])


def confirm_finish() -> str:
    return inline([
        [cb('🏁 Завершить', 'contest_finish', color='negative')],
        [cb('⬅️ Назад', 'staff_contest')],
    ])


def contest_settings_keyboard() -> str:
    return inline([
        [cb('✏️ Название', 'contest_edit_name'), cb('📝 Описание', 'contest_edit_description')],
        [cb('📅 Дата окончания', 'contest_edit_end_date')],
        [cb('🧩 Шаблон', 'contest_edit_template')],
        [cb('⬅️ Назад', 'staff_contest')],
    ])


def contest_template_settings() -> str:
    return inline([
        [cb('⭐ По конкурсным баллам', 'contest_set_template', color='primary', template='points')],
        [cb('⬅️ Назад', 'contest_settings')],
    ])


def users_list(rows: list[dict], page: int = 0) -> str:
    page_size = 8
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * page_size:(page + 1) * page_size]
    buttons: list[list[dict]] = []
    for i in range(0, len(chunk), 2):
        row = []
        for item in chunk[i:i + 2]:
            label = item.get('nickname') or item.get('name') or str(item['vk_id'])
            row.append(cb(label, 'user_open', target=int(item['vk_id']), page=page))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(cb('⬅️', 'staff_users', page=page - 1))
    if page + 1 < pages:
        nav.append(cb('➡️', 'staff_users', page=page + 1))
    if nav:
        buttons.append(nav)
    buttons.append([cb('➕ Добавить АП', 'user_add', color='positive')])
    buttons.append([cb('⬅️ Меню', 'staff_home')])
    return inline(buttons)


def user_card(target: int, *, in_contest: bool = False) -> str:
    rows = [
        [cb('🎭 Назначить должность', 'role_assign_user', target=target)],
    ]
    if in_contest:
        rows.append([cb('⭐ Баллы', 'points_user', color='positive', target=target), cb('➖ Из конкурса', 'member_remove_confirm', color='negative', target=target)])
    else:
        rows.append([cb('🏆 Добавить в конкурс', 'user_add_to_contest', color='positive', target=target)])
    rows.append([cb('🚫 Исключить из системы', 'user_remove_confirm', color='negative', target=target)])
    rows.append([cb('⬅️ К Агентам', 'staff_users')])
    return inline(rows)


def remove_user_confirm(target: int) -> str:
    return inline([
        [cb('✅ Исключить', 'user_remove', color='negative', target=target)],
        [cb('⬅️ Назад', 'user_open', target=target)],
    ])


def members_list(rows: list[dict], page: int = 0, *, action: str = 'member_open', page_action: str = 'staff_members') -> str:
    page_size = 8
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * page_size:(page + 1) * page_size]
    buttons: list[list[dict]] = []
    for i in range(0, len(chunk), 2):
        row = []
        for item in chunk[i:i + 2]:
            label = item.get('nickname') or item.get('vk_name') or str(item['vk_id'])
            row.append(cb(label, action, target=int(item['vk_id']), page=page))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(cb('⬅️', page_action, page=page - 1))
    if page + 1 < pages:
        nav.append(cb('➡️', page_action, page=page + 1))
    if nav:
        buttons.append(nav)
    buttons.append([cb('➕ Добавить', 'member_add', color='positive'), cb('⬅️ Меню', 'staff_home')])
    return inline(buttons)


def member_card(target: int) -> str:
    return inline([
        [cb('⭐ Управление баллами', 'points_user', color='positive', target=target)],
        [cb('🎭 Назначить должность', 'role_assign_user', target=target)],
        [cb('➖ Исключить из конкурса', 'member_remove_confirm', color='negative', target=target)],
        [cb('⬅️ К участникам', 'staff_members')],
    ])


def remove_member_confirm(target: int) -> str:
    return inline([
        [cb('✅ Исключить', 'member_remove', color='negative', target=target)],
        [cb('⬅️ Назад', 'member_open', target=target)],
    ])


def points_user(target: int) -> str:
    return inline([
        [cb('−10', 'points_quick', color='negative', target=target, amount=-10), cb('−5', 'points_quick', color='negative', target=target, amount=-5), cb('−1', 'points_quick', color='negative', target=target, amount=-1)],
        [cb('+1', 'points_quick', color='positive', target=target, amount=1), cb('+5', 'points_quick', color='positive', target=target, amount=5), cb('+10', 'points_quick', color='positive', target=target, amount=10)],
        [cb('✏️ Другое значение', 'points_custom', target=target)],
        [cb('⬅️ Назад', 'member_open', target=target)],
    ])


def roles_panel() -> str:
    return inline([
        [cb('➕ Создать должность', 'role_create', color='positive')],
        [cb('👤 Назначить должность', 'role_assign_start')],
        [cb('🔐 Настроить права', 'role_permissions_start', color='primary')],
        [cb('📋 Все должности', 'roles_list')],
        [cb('⬅️ Назад', 'staff_home')],
    ])


def role_picker(roles: Iterable[str], action: str, *, target: int | None = None, page: int = 0) -> str:
    names = list(roles)
    page_size = 8
    pages = max(1, (len(names) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = names[page * page_size:(page + 1) * page_size]
    buttons: list[list[dict]] = []
    for name in chunk:
        extra = {'role': name, 'page': page}
        if target is not None:
            extra['target'] = target
        buttons.append([cb(name, action, **extra)])
    nav = []
    if page > 0:
        nav.append(cb('⬅️', 'role_picker_page', mode=action, target=target or 0, page=page - 1))
    if page + 1 < pages:
        nav.append(cb('➡️', 'role_picker_page', mode=action, target=target or 0, page=page + 1))
    if nav:
        buttons.append(nav)
    buttons.append([cb('⬅️ Назад', 'staff_roles')])
    return inline(buttons)


def permissions_keyboard(role: str, catalog: dict[str, str], selected: set[str], page: int = 0) -> str:
    items = list(catalog.items())
    page_size = 6
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    buttons: list[list[dict]] = []
    for perm, label in items[page * page_size:(page + 1) * page_size]:
        mark = '✅' if perm in selected else '⬜'
        buttons.append([cb(f'{mark} {label}', 'perm_toggle', role=role, perm=perm, page=page)])
    nav = []
    if page > 0:
        nav.append(cb('⬅️', 'perm_page', role=role, page=page - 1))
    if page + 1 < pages:
        nav.append(cb('➡️', 'perm_page', role=role, page=page + 1))
    if nav:
        buttons.append(nav)
    buttons.append([cb('💾 Сохранить', 'perm_save', color='positive', role=role), cb('❌ Отмена', 'staff_roles', color='negative')])
    return inline(buttons)


def settings_panel() -> str:
    return inline([
        [cb('🔄 Обновить панель', 'staff_home', color='primary')],
        [cb('⬅️ Назад', 'staff_home')],
    ])
