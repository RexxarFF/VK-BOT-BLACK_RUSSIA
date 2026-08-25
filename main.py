import asyncio
import sys
from pathlib import Path

from loguru import logger

from app.config import settings

logger.remove()
logger.add(sys.stderr, level=settings.log_level)
settings.data_dir.mkdir(parents=True, exist_ok=True)
try:
    Path('logs').mkdir(parents=True, exist_ok=True)
    logger.add('logs/bot.log', rotation='10 MB', retention='14 days', level=settings.log_level, encoding='utf-8')
except Exception:
    pass

from app.bot import bot, svc, migrate_legacy_ui


async def main():
    await svc.bootstrap()
    await migrate_legacy_ui()
    logger.info('Запуск VK-бота Агентов Поддержки v3.3')
    logger.info('Бот работает через VK Long Poll.')
    try:
        await bot.run_polling()
    except Exception as exc:
        text = str(getattr(exc, 'error_msg', '') or exc).lower()
        if getattr(exc, 'code', None) == 100 and 'longpoll' in text:
            logger.error('Long Poll API выключен. Включи его в Управление → Работа с API → Long Poll API.')
        raise


if __name__ == '__main__':
    asyncio.run(main())
