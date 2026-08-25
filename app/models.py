from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


PERMISSION_CATALOG = {
    'profile.view': 'Просматривать свой профиль',
    'contest.view': 'Просматривать конкурс и рейтинг',
    'reports.submit': 'Подавать отчёты',
    'users.view': 'Просматривать участников',
    'users.add': 'Добавлять участников',
    'users.remove': 'Исключать участников',
    'points.add': 'Начислять баллы',
    'points.remove': 'Снимать баллы',
    'points.history': 'Просматривать историю баллов',
    'contest.create': 'Создавать конкурс',
    'contest.edit': 'Настраивать конкурс',
    'contest.finish': 'Завершать конкурс',
    'contest.members.add': 'Добавлять в конкурс',
    'contest.members.remove': 'Исключать из конкурса',
    'roles.view': 'Просматривать должности',
    'roles.create': 'Создавать должности',
    'roles.assign': 'Назначать должности',
    'roles.permissions': 'Настраивать права должностей',
    'settings.manage': 'Настраивать беседы и бота',
    'logs.view': 'Просматривать служебную информацию',
}


DEFAULT_ROLES = {
    'Владелец': {
        'level': 100,
        'permissions': ['*'],
        'system': True,
    },
    'Главный Следящий': {
        'level': 90,
        'permissions': [
            'profile.view', 'contest.view', 'reports.submit',
            'users.view', 'users.add', 'users.remove',
            'points.add', 'points.remove', 'points.history',
            'contest.create', 'contest.edit', 'contest.finish',
            'contest.members.add', 'contest.members.remove',
            'roles.view', 'roles.create', 'roles.assign', 'roles.permissions',
            'settings.manage', 'logs.view',
        ],
        'system': True,
    },
    'Заместитель ГС': {
        'level': 80,
        'permissions': [
            'profile.view', 'contest.view', 'reports.submit',
            'users.view', 'users.add', 'users.remove',
            'points.add', 'points.remove', 'points.history',
            'contest.create', 'contest.edit', 'contest.finish',
            'contest.members.add', 'contest.members.remove',
            'roles.view', 'roles.assign', 'logs.view',
        ],
        'system': True,
    },
    'Следящий': {
        'level': 60,
        'permissions': [
            'profile.view', 'contest.view', 'reports.submit',
            'users.view', 'users.add',
            'points.add', 'points.remove', 'points.history',
            'contest.members.add', 'contest.members.remove',
            'roles.view', 'logs.view',
        ],
        'system': True,
    },
    'Старший Агент': {
        'level': 40,
        'permissions': ['profile.view', 'contest.view', 'reports.submit', 'users.view'],
        'system': True,
    },
    'Агент Поддержки': {
        'level': 10,
        'permissions': ['profile.view', 'contest.view', 'reports.submit'],
        'system': True,
    },
}


CONTEST_TEMPLATES = {
    'points': {
        'name': 'По конкурсным баллам',
        'description': 'Чем больше конкурсных баллов набрал участник, тем выше его место.',
    }
}


DEFAULT_SETTINGS = {
    'peers': {
        'reports': None,
        'leadership': None,
        'logs': None,
    },
    'panel_messages': {
        'reports': None,
        'leadership': None,
    },
    'active_contest_id': None,
    'contest_seq': 0,
    'report_seq': 0,
}
