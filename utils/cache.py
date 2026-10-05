import asyncio
import logging
import time
import traceback
from pathlib import Path

from context import config
from utils.tracing import get_tracer, traced


logger = logging.getLogger('cache')


def cache_file(media: bytes, filename: str):
    cache_path = config.cache_dir / filename
    with cache_path.open('wb') as f:
        f.write(media)
    logger.info(f'cached {cache_path}')
    return cache_path


def is_cached(filename: str) -> tuple[Path, bool]:
    cache_path = config.cache_dir / filename
    logger.info(f'checking cache for {cache_path}')
    return cache_path, cache_path.exists()


async def cache_cleaner():
    tracer = get_tracer('utils.cache')
    while True:
        with tracer.start_as_current_span('cache.cleaner_tick'):
            logger.info(f'running cache cleaner')
        try:
            removed = 0
            for item in config.cache_dir.iterdir():
                if time.time() - item.stat().st_mtime > 10*24*60*60:
                    item.unlink()
                    removed += 1
            logger.info(f'removed {removed} cached files')
        except Exception as e:
            traceback.print_exception(e)
        finally:
            await asyncio.sleep(10*60)
