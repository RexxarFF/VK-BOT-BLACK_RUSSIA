import pytest

from app.services import AppService
from app.storage.json_store import JsonStore


@pytest.mark.asyncio
async def test_contest_points_and_profile_places(tmp_path):
    svc = AppService(JsonStore(tmp_path), {1})
    await svc.bootstrap()
    await svc.ensure_user(1, 'Owner', registered=True)
    await svc.register_user(1, 2, 'Helper One', 'Helper_One')
    await svc.register_user(1, 3, 'Helper Two', 'Helper_Two')

    cid = await svc.create_contest(1, 'Weekly contest', 'Test contest description', '31.12.2099', 'points')
    assert cid == 1
    await svc.add_member(1, 2, 'Helper One', 'Helper_One')
    await svc.add_member(1, 3, 'Helper Two', 'Helper_Two')
    await svc.change_points(1, 2, 10)
    await svc.change_points(1, 3, 5)

    contest, ranking = await svc.ranking()
    assert contest['template'] == 'points'
    assert ranking[0]['vk_id'] == 2
    assert ranking[0]['place'] == 1
    assert ranking[1]['place'] == 2

    await svc.finish_contest(1)
    stats = await svc.profile_stats(2)
    assert stats['first'] == 1
    assert stats['contests'] == 1


@pytest.mark.asyncio
async def test_custom_role_permissions_and_assignment(tmp_path):
    svc = AppService(JsonStore(tmp_path), {1})
    await svc.bootstrap()
    await svc.ensure_user(1, 'Owner', registered=True)
    await svc.register_user(1, 2, 'Helper', 'Helper_Test')
    await svc.create_role(1, 'Организатор', 55)
    await svc.set_role_permissions(1, 'Организатор', ['contest.view', 'points.add'])
    await svc.assign_role(1, 2, 'Организатор')

    assert await svc.permission(2, 'points.add') is True
    assert await svc.permission(2, 'points.remove') is False


@pytest.mark.asyncio
async def test_report_creation_requires_contest_member(tmp_path):
    svc = AppService(JsonStore(tmp_path), {1})
    await svc.bootstrap()
    await svc.ensure_user(1, 'Owner', registered=True)
    await svc.register_user(1, 2, 'Helper', 'Helper_Test')
    await svc.create_contest(1, 'Weekly contest', 'Test contest description', '31.12.2099', 'points')
    await svc.add_member(1, 2, 'Helper', 'Helper_Test')
    report = await svc.create_report(2, 'Helper_Test', '24.08.2026', 'photo1_2_test')
    assert report['nickname'] == 'Helper_Test'
    assert report['status'] == 'submitted'

@pytest.mark.asyncio
async def test_edit_contest_settings(tmp_path):
    svc = AppService(JsonStore(tmp_path), {1})
    await svc.bootstrap()
    await svc.ensure_user(1, 'Owner', registered=True)
    await svc.create_contest(1, 'Weekly contest', 'Test contest description', '31.12.2099', 'points')

    updated = await svc.edit_contest(1, 'name', 'Best Support Agent')
    assert updated['name'] == 'Best Support Agent'
    updated = await svc.edit_contest(1, 'description', 'Updated contest description')
    assert updated['description'] == 'Updated contest description'


@pytest.mark.asyncio
async def test_registered_users_can_be_managed_separately_from_contest(tmp_path):
    svc = AppService(JsonStore(tmp_path), {1})
    await svc.bootstrap()
    await svc.ensure_user(1, 'Owner', registered=True)
    await svc.register_user(1, 2, 'Helper', 'Helper_Test')
    users = await svc.active_users()
    assert any(u['vk_id'] == 2 for u in users)

    await svc.create_contest(1, 'Weekly contest', 'Test contest description', '31.12.2099', 'points')
    await svc.add_registered_user_to_contest(1, 2)
    card = await svc.user_card(2)
    assert card['in_active_contest'] is True

    await svc.deactivate_user(1, 2)
    user = await svc.get_user(2)
    assert user['active'] is False
    contest = await svc.active_contest()
    assert '2' not in contest['members']
