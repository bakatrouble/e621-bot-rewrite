import asyncio
import logging
import time
import traceback
from pathlib import Path

from context import AppContext
from utils.tracing import get_tracer, set_span_attributes

logger = logging.getLogger('cache')


def cache_file(ctx: AppContext, media: bytes, filename: str):
    cache_path = ctx.config.cache_dir / filename
    with cache_path.open('wb') as f:
        f.write(media)
    logger.info(f'cached {cache_path}')
    return cache_path


def is_cached(ctx: AppContext, filename: str) -> tuple[Path, bool]:
    cache_path = ctx.config.cache_dir / filename
    logger.info(f'checking cache for {cache_path}')
    return cache_path, cache_path.exists()


async def cache_cleaner(ctx: AppContext):
    tracer = get_tracer('utils.cache')
    while True:
        with tracer.start_as_current_span('cache.cleaner_tick'):
            logger.info('running cache cleaner')
            try:
                removed = 0
                for item in ctx.config.cache_dir.iterdir():
                    if time.time() - item.stat().st_mtime > 10 * 24 * 60 * 60:
                        item.unlink()
                        removed += 1
                logger.info(f'removed {removed} cached files')
                set_span_attributes({'cache.removed': removed})
            except Exception as e:  # noqa: BLE001
                traceback.print_exception(e)
        await asyncio.sleep(10 * 60)
