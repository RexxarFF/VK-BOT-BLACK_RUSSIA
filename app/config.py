from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _ints(value: str) -> frozenset[int]:
    result = set()
    for part in (value or '').split(','):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return frozenset(result)


@dataclass(frozen=True)
class Settings:
    token: str = os.getenv('VK_GROUP_TOKEN', '')
    group_id: int = int(os.getenv('VK_GROUP_ID', '0') or 0)
    owner_ids: frozenset[int] = _ints(os.getenv('BOT_OWNER_IDS', ''))
    data_dir: Path = Path(os.getenv('DATA_DIR', 'data'))
    log_level: str = os.getenv('LOG_LEVEL', 'INFO')
    max_points_change: int = int(os.getenv('MAX_POINTS_CHANGE', '1000') or 1000)


settings = Settings()
