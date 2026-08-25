from __future__ import annotations

from typing import Any

from .errors import ConflictError, NotFoundError, PermissionDenied, ValidationError
from .models import CONTEST_TEMPLATES, DEFAULT_ROLES, DEFAULT_SETTINGS, PERMISSION_CATALOG, now_iso
from .storage.json_store import JsonStore
from .validators import clean_text, parse_contest_date, parse_level, parse_points, parse_report_date, validate_nickname


class AppService:
    def __init__(self, store: JsonStore, owner_ids: set[int] | frozenset[int]):
        self.store = store
        self.owner_ids = set(owner_ids)

    async def bootstrap(self):
        roles = await self.store.read('roles', {})
        if not roles:
            await self.store.write('roles', DEFAULT_ROLES)
        else:
            def merge_roles(data):
                for name, role in DEFAULT_ROLES.items():
                    if name not in data:
                        data[name] = role
                    elif data[name].get('system'):
                        data[name]['level'] = role['level']
                        data[name]['system'] = True
                        existing = list(data[name].get('permissions', []))
                        for perm in role['permissions']:
                            if perm not in existing:
                                existing.append(perm)
                        data[name]['permissions'] = existing
                # v3.1 migration: every role now uses the compact 1..10 hierarchy.
                # Old custom roles from earlier builds are clamped automatically.
                for role in data.values():
                    try:
                        role['level'] = max(1, min(10, int(role.get('level', 1))))
                    except (TypeError, ValueError):
                        role['level'] = 1
                return data
            await self.store.update('roles', DEFAULT_ROLES, merge_roles)

        def merge_settings(data):
            data.setdefault('peers', {})
            for key, value in DEFAULT_SETTINGS['peers'].items():
                data['peers'].setdefault(key, value)
            data.setdefault('panel_messages', {})
            for key, value in DEFAULT_SETTINGS['panel_messages'].items():
                data['panel_messages'].setdefault(key, value)
            data.setdefault('active_contest_id', None)
            data.setdefault('contest_seq', 0)
            data.setdefault('report_seq', 0)
            return data
        await self.store.update('settings', DEFAULT_SETTINGS, merge_settings)
        # Migrate data from v1/v2 without breaking existing contests/users.
        contests = await self.store.read('contests', {})
        contest_member_ids = {uid for c in contests.values() for uid in c.get('members', {}).keys()}

        def migrate_users(data):
            for uid, user in data.items():
                user.setdefault('placements', [])
                user.setdefault('active', True)
                if 'registered' not in user:
                    user['registered'] = bool(user.get('nickname')) or uid in contest_member_ids or int(uid) in self.owner_ids
            return data
        await self.store.update('users', {}, migrate_users)

        def migrate_contests(data):
            for contest in data.values():
                contest.setdefault('template', 'points')
                contest.setdefault('final_ranking', [])
                contest.setdefault('finished_at', None)
            return data
        await self.store.update('contests', {}, migrate_contests)
        await self.store.update('reports', {}, lambda data: data)
        await self.store.update('audit_log', [], lambda data: data)

        # Owners always exist and always keep owner role.
        for owner_id in self.owner_ids:
            await self.ensure_user(owner_id, registered=True)

    async def ensure_user(self, vk_id: int, name: str = '', *, registered: bool = False) -> dict[str, Any]:
        if int(vk_id) <= 0:
            raise ValidationError('Некорректный VK ID пользователя.')

        def mutate(users):
            key = str(vk_id)
            if key not in users:
                users[key] = {
                    'vk_id': int(vk_id),
                    'name': name or f'VK {vk_id}',
                    'nickname': '',
                    'role': 'Владелец' if vk_id in self.owner_ids else 'Агент Поддержки',
                    'registered': bool(registered or vk_id in self.owner_ids),
                    'active': True,
                    'created_at': now_iso(),
                    'placements': [],
                }
            else:
                users[key].setdefault('nickname', '')
                users[key].setdefault('role', 'Агент Поддержки')
                users[key].setdefault('registered', False)
                users[key].setdefault('active', True)
                users[key].setdefault('placements', [])
                if registered:
                    users[key]['registered'] = True
                if name:
                    users[key]['name'] = name
            if vk_id in self.owner_ids:
                users[key]['role'] = 'Владелец'
                users[key]['registered'] = True
                users[key]['active'] = True
        users = await self.store.update('users', {}, mutate)
        return users[str(vk_id)]

    async def register_user(self, actor: int, vk_id: int, name: str, nickname: str) -> dict:
        await self.require(actor, 'users.add')
        nickname = validate_nickname(nickname)
        existing = await self.get_user(vk_id)
        if existing and existing.get('registered') and existing.get('active'):
            raise ConflictError(f'Этот пользователь уже зарегистрирован как {existing.get("nickname") or existing.get("name")}.')
        await self.ensure_user(vk_id, name, registered=True)

        def mutate(users):
            item = users[str(vk_id)]
            item['name'] = name or item.get('name') or f'VK {vk_id}'
            item['nickname'] = nickname
            item['registered'] = True
            item['active'] = True
        users = await self.store.update('users', {}, mutate)
        await self.audit('user.register', actor, {'target': vk_id, 'nickname': nickname})
        return users[str(vk_id)]

    async def get_user(self, vk_id: int) -> dict | None:
        return (await self.store.read('users', {})).get(str(vk_id))

    async def active_users(self) -> list[dict]:
        users = await self.store.read('users', {})
        result = [dict(v) for v in users.values() if v.get('registered') and v.get('active')]
        result.sort(key=lambda x: (x.get('nickname') or x.get('name') or '').lower())
        return result

    async def user_card(self, vk_id: int) -> dict:
        user = await self.get_user(vk_id)
        if not user or not user.get('registered') or not user.get('active'):
            raise NotFoundError('Этот пользователь не зарегистрирован в системе Агентов Поддержки.')
        contest = await self.active_contest()
        member = (contest or {}).get('members', {}).get(str(vk_id)) if contest else None
        return {
            **dict(user),
            'in_active_contest': member is not None,
            'active_points': int(member.get('points', 0)) if member else None,
            'active_contest_name': contest.get('name') if contest and member else None,
        }

    async def add_registered_user_to_contest(self, actor: int, target: int) -> dict:
        user = await self.get_user(target)
        if not user or not user.get('registered') or not user.get('active'):
            raise NotFoundError('Сначала добавь этого человека в систему через /adduser.')
        nickname = user.get('nickname') or ''
        if not nickname:
            raise ValidationError('У этого Агента не указан Nick_Name. Добавь его заново через /adduser.')
        return await self.add_member(actor, target, user.get('name') or f'VK {target}', nickname)

    async def deactivate_user(self, actor: int, target: int):
        await self.require(actor, 'users.remove')
        if target in self.owner_ids:
            raise PermissionDenied('Владельца нельзя исключить из системы.')
        user = await self.get_user(target)
        if not user or not user.get('registered') or not user.get('active'):
            raise NotFoundError('Этот пользователь не зарегистрирован или уже исключён из системы.')
        roles = await self.roles()
        actor_user = await self.get_user(actor)
        actor_level = 10 if actor in self.owner_ids else int(roles.get((actor_user or {}).get('role'), {}).get('level', 0))
        target_level = int(roles.get(user.get('role'), {}).get('level', 0))
        if actor not in self.owner_ids and target_level >= actor_level:
            raise PermissionDenied('Нельзя исключить пользователя с должностью твоего уровня или выше.')

        def mutate_users(users):
            users[str(target)]['active'] = False
        await self.store.update('users', {}, mutate_users)

        contest = await self.active_contest()
        if contest and str(target) in contest.get('members', {}):
            cid = str(contest['id'])
            def mutate_contests(data):
                data[cid].get('members', {}).pop(str(target), None)
            await self.store.update('contests', {}, mutate_contests)
        await self.audit('user.deactivate', actor, {'target': target})

    async def set_nickname(self, vk_id: int, nickname: str):
        nickname = validate_nickname(nickname)
        await self.ensure_user(vk_id)
        def mutate(users):
            users[str(vk_id)]['nickname'] = nickname
        await self.store.update('users', {}, mutate)

    async def permission(self, vk_id: int, permission: str) -> bool:
        if vk_id in self.owner_ids:
            return True
        user = await self.get_user(vk_id)
        if not user or not user.get('registered') or not user.get('active'):
            return False
        roles = await self.store.read('roles', DEFAULT_ROLES)
        role = roles.get(user.get('role'), {})
        perms = role.get('permissions', [])
        if '*' in perms or permission in perms:
            return True
        prefix = permission.split('.')[0] + '.*'
        return prefix in perms

    async def require(self, vk_id: int, permission: str):
        if not await self.permission(vk_id, permission):
            raise PermissionDenied()

    async def is_staff(self, vk_id: int) -> bool:
        if vk_id in self.owner_ids:
            return True
        staff_permissions = (
            'users.add', 'points.add', 'points.remove', 'contest.create',
            'contest.members.add', 'roles.assign', 'settings.manage',
        )
        return any([await self.permission(vk_id, p) for p in staff_permissions])

    async def audit(self, action: str, actor: int, details: dict[str, Any]):
        def mutate(items):
            items.append({'at': now_iso(), 'action': action, 'actor': int(actor), 'details': details})
            if len(items) > 5000:
                del items[:-5000]
        await self.store.update('audit_log', [], mutate)

    async def audit_entries(self, actor: int, limit: int = 25) -> list[dict]:
        await self.require(actor, 'logs.view')
        items = await self.store.read('audit_log', [])
        limit = max(1, min(int(limit), 50))
        return list(reversed(items[-limit:]))

    # ---------- Roles / permissions ----------

    async def roles(self) -> dict:
        return await self.store.read('roles', DEFAULT_ROLES)

    async def resolve_role_name(self, value: str) -> str:
        roles = await self.roles()
        raw = (value or '').strip()
        if raw in roles:
            return raw
        low = raw.casefold()
        for name in roles:
            if name.casefold() == low:
                return name
        raise NotFoundError('Такой должности не существует.', code='role_not_found')

    async def create_role(self, actor: int, name: str, level: int) -> dict:
        await self.require(actor, 'roles.create')
        name = clean_text(name, field='Название должности', min_len=2, max_len=50)
        level = parse_level(level)
        roles = await self.roles()
        if any(existing.casefold() == name.casefold() for existing in roles):
            raise ConflictError('Должность с таким названием уже существует.')
        actor_user = await self.get_user(actor)
        actor_level = 10 if actor in self.owner_ids else int(roles.get((actor_user or {}).get('role'), {}).get('level', 0))
        if actor not in self.owner_ids and level >= actor_level:
            raise PermissionDenied('Нельзя создать должность своего уровня или выше.')
        def mutate(data):
            data[name] = {'level': level, 'permissions': [], 'system': False}
        roles = await self.store.update('roles', DEFAULT_ROLES, mutate)
        await self.audit('role.create', actor, {'name': name, 'level': level})
        return roles[name]

    async def set_role_permissions(self, actor: int, role_name: str, permissions: list[str]) -> dict:
        await self.require(actor, 'roles.permissions')
        roles = await self.roles()
        role_name = await self.resolve_role_name(role_name)
        if role_name == 'Владелец':
            raise PermissionDenied('Права владельца менять нельзя.')
        if roles[role_name].get('system') and actor not in self.owner_ids:
            raise PermissionDenied('Изменять права системных должностей может только владелец.')
        invalid = [p for p in permissions if p not in PERMISSION_CATALOG]
        if invalid:
            raise ValidationError('В списке есть неизвестные права.')
        if actor not in self.owner_ids:
            for perm in permissions:
                if not await self.permission(actor, perm):
                    raise PermissionDenied('Нельзя выдать должности право, которого нет у тебя.')
        clean = sorted(set(permissions))
        def mutate(data):
            data[role_name]['permissions'] = clean
        roles = await self.store.update('roles', DEFAULT_ROLES, mutate)
        await self.audit('role.permissions', actor, {'role': role_name, 'permissions': clean})
        return roles[role_name]

    async def assign_role(self, actor: int, target: int, role_name: str):
        await self.require(actor, 'roles.assign')
        roles = await self.roles()
        role_name = await self.resolve_role_name(role_name)
        if role_name == 'Владелец' and target not in self.owner_ids:
            raise PermissionDenied('Должность «Владелец» назначается только через BOT_OWNER_IDS в настройках бота.')
        target_user = await self.get_user(target)
        if not target_user or not target_user.get('registered'):
            raise NotFoundError('Сначала добавь этого человека в систему через /adduser или /addmember.')
        actor_user = await self.get_user(actor)
        actor_level = 10 if actor in self.owner_ids else int(roles.get((actor_user or {}).get('role'), {}).get('level', 0))
        target_level = int(roles.get(target_user.get('role'), {}).get('level', 0))
        new_level = int(roles[role_name].get('level', 0))
        if actor not in self.owner_ids and (target_level >= actor_level or new_level >= actor_level):
            raise PermissionDenied('Твоя должность недостаточно высокая для такого назначения.')
        def mutate(users):
            users[str(target)]['role'] = role_name
        await self.store.update('users', {}, mutate)
        await self.audit('role.assign', actor, {'target': target, 'role': role_name})

    # ---------- Contests ----------

    async def create_contest(self, actor: int, name: str, description: str, end_date: str, template: str = 'points') -> int:
        await self.require(actor, 'contest.create')
        name = clean_text(name, field='Название конкурса', min_len=3, max_len=80)
        description = clean_text(description, field='Описание конкурса', min_len=3, max_len=1200)
        end_date = parse_contest_date(end_date)
        if template not in CONTEST_TEMPLATES:
            raise ValidationError('Неизвестный шаблон конкурса.')
        if await self.active_contest():
            raise ConflictError('Уже есть активный конкурс. Сначала заверши его.')
        box = {'id': 0}
        def bump(settings):
            settings['contest_seq'] = int(settings.get('contest_seq', 0)) + 1
            box['id'] = settings['contest_seq']
            settings['active_contest_id'] = box['id']
        await self.store.update('settings', DEFAULT_SETTINGS, bump)
        cid = box['id']
        def mutate(data):
            data[str(cid)] = {
                'id': cid,
                'name': name,
                'description': description,
                'end_date': end_date,
                'template': template,
                'status': 'active',
                'members': {},
                'created_by': actor,
                'created_at': now_iso(),
                'finished_at': None,
                'final_ranking': [],
            }
        await self.store.update('contests', {}, mutate)
        await self.audit('contest.create', actor, {'contest_id': cid, 'name': name, 'template': template})
        return cid

    async def edit_contest(self, actor: int, field: str, value: str) -> dict:
        await self.require(actor, 'contest.edit')
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса.')
        cid = str(contest['id'])
        if field == 'name':
            value = clean_text(value, field='Название конкурса', min_len=3, max_len=80)
        elif field == 'description':
            value = clean_text(value, field='Описание конкурса', min_len=3, max_len=1200)
        elif field == 'end_date':
            value = parse_contest_date(value)
        elif field == 'template':
            if value not in CONTEST_TEMPLATES:
                raise ValidationError('Неизвестный шаблон конкурса.')
        else:
            raise ValidationError('Неизвестная настройка конкурса.')
        def mutate(data):
            data[cid][field] = value
        contests = await self.store.update('contests', {}, mutate)
        await self.audit('contest.edit', actor, {'contest_id': int(cid), 'field': field, 'value': value})
        return contests[cid]

    async def active_contest(self) -> dict | None:
        settings = await self.store.read('settings', DEFAULT_SETTINGS)
        cid = settings.get('active_contest_id')
        if not cid:
            return None
        contest = (await self.store.read('contests', {})).get(str(cid))
        if contest and contest.get('status') == 'active':
            return contest
        return None

    async def add_member(self, actor: int, target: int, target_name: str, nickname: str) -> dict:
        await self.require(actor, 'contest.members.add')
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса. Сначала создай конкурс.', code='no_active_contest')
        nickname = validate_nickname(nickname)
        cid = str(contest['id'])
        if str(target) in contest.get('members', {}):
            raise ConflictError('Этот Агент уже участвует в активном конкурсе.')
        existing_user = await self.get_user(target)
        if existing_user and existing_user.get('registered'):
            # Keep current role, but update the explicitly supplied game nickname.
            await self.set_nickname(target, nickname)
            if target_name:
                await self.ensure_user(target, target_name, registered=True)
        else:
            # Adding to a contest also registers the Agent in the bot. This requires
            # contest.members.add, not a second unrelated users.add permission.
            await self.ensure_user(target, target_name, registered=True)
            def register(users):
                item = users[str(target)]
                item['name'] = target_name or item.get('name') or f'VK {target}'
                item['nickname'] = nickname
                item['registered'] = True
                item['active'] = True
            await self.store.update('users', {}, register)
            await self.audit('user.register.from_contest', actor, {'target': target, 'nickname': nickname})
        def mutate(data):
            data[cid]['members'][str(target)] = {
                'points': 0,
                'history': [],
                'joined_at': now_iso(),
                'joined_by': actor,
            }
        await self.store.update('contests', {}, mutate)
        await self.audit('contest.member.add', actor, {'contest_id': int(cid), 'target': target, 'nickname': nickname})
        return (await self.get_user(target)) or {}

    async def remove_member(self, actor: int, target: int):
        await self.require(actor, 'contest.members.remove')
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса.')
        cid = str(contest['id'])
        if str(target) not in contest.get('members', {}):
            raise NotFoundError('Этот пользователь не участвует в активном конкурсе.')
        def mutate(data):
            del data[cid]['members'][str(target)]
        await self.store.update('contests', {}, mutate)
        await self.audit('contest.member.remove', actor, {'contest_id': int(cid), 'target': target})

    async def change_points(self, actor: int, target: int, amount: int, reason: str = '') -> tuple[int, int]:
        amount = parse_points(amount)
        await self.require(actor, 'points.add' if amount > 0 else 'points.remove')
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса.')
        cid = str(contest['id'])
        member = contest.get('members', {}).get(str(target))
        if not member:
            raise NotFoundError('Этот Агент не участвует в активном конкурсе.')
        before = int(member.get('points', 0))
        after = before + amount
        if after < 0:
            raise ValidationError(f'Нельзя снять {abs(amount)} баллов: у участника сейчас только {before}.')
        reason = (reason or 'Изменение через панель руководства').strip()[:300]
        def mutate(data):
            entry = data[cid]['members'][str(target)]
            entry['points'] = after
            entry.setdefault('history', []).append({
                'at': now_iso(), 'actor': actor, 'amount': amount, 'before': before, 'after': after, 'reason': reason,
            })
        await self.store.update('contests', {}, mutate)
        await self.audit('points.change', actor, {'contest_id': int(cid), 'target': target, 'amount': amount, 'before': before, 'after': after, 'reason': reason})
        return before, after

    async def participants(self) -> tuple[dict | None, list[dict]]:
        contest = await self.active_contest()
        if not contest:
            return None, []
        users = await self.store.read('users', {})
        rows = []
        for uid, member in contest.get('members', {}).items():
            user = users.get(uid, {})
            rows.append({
                'vk_id': int(uid),
                'nickname': user.get('nickname') or user.get('name') or f'VK {uid}',
                'vk_name': user.get('name') or f'VK {uid}',
                'points': int(member.get('points', 0)),
                'role': user.get('role', 'Агент Поддержки'),
            })
        rows.sort(key=lambda x: (-x['points'], x['nickname'].lower()))
        return contest, rows

    async def ranking(self, contest_id: int | None = None) -> tuple[dict | None, list[dict]]:
        if contest_id is None:
            contest = await self.active_contest()
        else:
            contest = (await self.store.read('contests', {})).get(str(contest_id))
        if not contest:
            return None, []
        users = await self.store.read('users', {})
        raw = []
        for uid, member in contest.get('members', {}).items():
            user = users.get(uid, {})
            raw.append({
                'vk_id': int(uid),
                'nickname': user.get('nickname') or user.get('name') or f'VK {uid}',
                'points': int(member.get('points', 0)),
            })
        raw.sort(key=lambda x: (-x['points'], x['nickname'].lower()))
        previous_points = None
        previous_place = 0
        for index, row in enumerate(raw, 1):
            if previous_points is None or row['points'] != previous_points:
                previous_place = index
            row['place'] = previous_place
            previous_points = row['points']
        return contest, raw

    async def finish_contest(self, actor: int) -> list[dict]:
        await self.require(actor, 'contest.finish')
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса.')
        cid = int(contest['id'])
        _, ranking = await self.ranking(cid)
        def mutate_contests(data):
            item = data[str(cid)]
            item['status'] = 'finished'
            item['finished_at'] = now_iso()
            item['final_ranking'] = ranking
        await self.store.update('contests', {}, mutate_contests)
        def mutate_users(users):
            for row in ranking:
                key = str(row['vk_id'])
                if key not in users:
                    continue
                users[key].setdefault('placements', []).append({
                    'contest_id': cid,
                    'contest_name': contest['name'],
                    'place': row['place'],
                    'points': row['points'],
                    'at': now_iso(),
                })
        await self.store.update('users', {}, mutate_users)
        def clear(settings):
            settings['active_contest_id'] = None
        await self.store.update('settings', DEFAULT_SETTINGS, clear)
        await self.audit('contest.finish', actor, {'contest_id': cid, 'ranking': ranking[:10]})
        return ranking

    async def profile_stats(self, vk_id: int) -> dict:
        user = await self.get_user(vk_id)
        if not user:
            user = await self.ensure_user(vk_id)
        placements = user.get('placements', [])
        first = sum(1 for p in placements if int(p.get('place', 0)) == 1)
        second = sum(1 for p in placements if int(p.get('place', 0)) == 2)
        third = sum(1 for p in placements if int(p.get('place', 0)) == 3)
        active = await self.active_contest()
        points = None
        place = None
        if active and str(vk_id) in active.get('members', {}):
            points = int(active['members'][str(vk_id)].get('points', 0))
            _, ranking = await self.ranking()
            place = next((r['place'] for r in ranking if r['vk_id'] == vk_id), None)
        return {
            'user': user,
            'first': first,
            'second': second,
            'third': third,
            'contests': len(placements),
            'active_points': points,
            'active_place': place,
        }

    # ---------- Reports ----------

    async def create_report(self, submitter: int, nickname: str, work_date: str, proof_attachment: str) -> dict:
        await self.require(submitter, 'reports.submit')
        nickname = validate_nickname(nickname)
        work_date = parse_report_date(work_date)
        contest = await self.active_contest()
        if not contest:
            raise NotFoundError('Сейчас нет активного конкурса, поэтому отчёт отправить нельзя.')
        if str(submitter) not in contest.get('members', {}):
            raise PermissionDenied('Ты не добавлен в активный конкурс. Обратись к руководству АП.')
        if not proof_attachment.startswith('photo'):
            raise ValidationError('Доказательство должно быть фотографией, отправленной прямо в VK.')
        await self.set_nickname(submitter, nickname)
        box = {'id': 0}
        def bump(settings):
            settings['report_seq'] = int(settings.get('report_seq', 0)) + 1
            box['id'] = settings['report_seq']
        await self.store.update('settings', DEFAULT_SETTINGS, bump)
        report_id = box['id']
        def mutate(data):
            data[str(report_id)] = {
                'id': report_id,
                'submitter': submitter,
                'nickname': nickname,
                'work_date': work_date,
                'proof_attachment': proof_attachment,
                'contest_id': int(contest['id']),
                'status': 'submitted',
                'created_at': now_iso(),
            }
        await self.store.update('reports', {}, mutate)
        await self.audit('report.submit', submitter, {'report_id': report_id, 'contest_id': int(contest['id'])})
        return (await self.store.read('reports', {}))[str(report_id)]

    # ---------- Settings ----------

    async def settings(self) -> dict:
        return await self.store.read('settings', DEFAULT_SETTINGS)

    async def set_peer(self, actor: int, kind: str, peer_id: int) -> str:
        await self.require(actor, 'settings.manage')
        aliases = {
            'reports': 'reports', 'report': 'reports',
            'leadership': 'leadership', 'staff': 'leadership', 'admin': 'leadership',
            'logs': 'logs', 'log': 'logs',
        }
        key = aliases.get((kind or '').strip().lower())
        if key not in DEFAULT_SETTINGS['peers']:
            raise ValidationError('Тип беседы должен быть: reports, leadership или logs.')
        if int(peer_id) < 2_000_000_000:
            raise ValidationError('Команду /setchat нужно отправить именно в беседе VK.')
        def mutate(settings):
            settings.setdefault('peers', {})[key] = int(peer_id)
        await self.store.update('settings', DEFAULT_SETTINGS, mutate)
        await self.audit('settings.peer', actor, {'kind': key, 'peer_id': int(peer_id)})
        return key

    async def set_panel_message(self, kind: str, message_id: int | None):
        def mutate(settings):
            settings.setdefault('panel_messages', {})[kind] = message_id
        await self.store.update('settings', DEFAULT_SETTINGS, mutate)

    async def ui_schema_version(self) -> int:
        cfg = await self.settings()
        return int(cfg.get('ui_schema_version', 0) or 0)

    async def set_ui_schema_version(self, version: int):
        def mutate(settings):
            settings['ui_schema_version'] = int(version)
        await self.store.update('settings', DEFAULT_SETTINGS, mutate)

    async def permission_catalog(self) -> dict[str, str]:
        return dict(PERMISSION_CATALOG)
