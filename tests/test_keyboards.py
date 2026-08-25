import json

from app.keyboards import (
    helper_menu,
    leadership_main,
    members_list,
    permissions_keyboard,
    points_user,
    report_chat_panel,
    user_card,
    users_list,
)
from app.models import PERMISSION_CATALOG


def assert_keyboard(raw: str):
    data = json.loads(raw)
    assert data['inline'] is True
    assert data['buttons']
    assert len(data['buttons']) <= 10
    for row in data['buttons']:
        assert 1 <= len(row) <= 4
        for button in row:
            payload = json.loads(button['action']['payload'])
            assert payload['action']
            assert len(button['action']['label']) <= 40


def test_main_keyboards_are_valid_json():
    assert_keyboard(helper_menu(False))
    assert_keyboard(helper_menu(True))
    assert_keyboard(leadership_main())
    assert_keyboard(report_chat_panel())
    assert_keyboard(points_user(123))
    assert_keyboard(user_card(123, in_contest=True))


def test_paginated_lists_fit_vk_keyboard_limits():
    rows = [
        {'vk_id': i, 'nickname': f'Helper_{i}', 'name': f'VK {i}', 'vk_name': f'VK {i}', 'points': i, 'role': 'Агент Поддержки'}
        for i in range(1, 30)
    ]
    assert_keyboard(users_list(rows, 0))
    assert_keyboard(users_list(rows, 3))
    assert_keyboard(members_list(rows, 0))
    assert_keyboard(permissions_keyboard('Организатор', PERMISSION_CATALOG, {'points.add'}, 0))
