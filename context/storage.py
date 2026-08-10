import logging
from dataclasses import dataclass

from opentelemetry import trace
from redis import RedisError
from redis.asyncio import Redis


# tracer = trace.get_tracer('subscriber.storage')


@dataclass
class StorageDump:
    subs: list[str]
    sent: list[int]
    last_post_version: int


class Storage:
    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self.lock = self._redis.lock('subscriber:lock')
        self._logger = logging.getLogger('storage')

    async def get_subs(self) -> list[str]:
        # with tracer.start_as_current_span('get_subs'):
        return list(sorted(await self._redis.smembers('subscriber:e621:subs')))

    async def add_sub(self, sub: str):
        # with tracer.start_as_current_span('add_sub') as span:
        #     span.set_attribute('sub', sub)
        await self._redis.sadd('subscriber:e621:subs', sub)

    async def remove_sub(self, sub: str):
        # with tracer.start_as_current_span('remove_sub') as span:
        #     span.set_attribute('sub', sub)
        await self._redis.srem('subscriber:e621:subs', sub)

    async def get_post_sent(self, post_ids: list[int]) -> dict[int, bool]:
        # with tracer.start_as_current_span('get_post_sent') as span:
        #     span.set_attribute('post_ids', post_ids)
        if not post_ids:
            return {}
        return {post_id: bool(ismember)
                for post_id, ismember
                in zip(post_ids, await self._redis.smismember('subscriber:e621:sent', post_ids))}

    async def set_post_sent(self, post_id: int):
        # with tracer.start_as_current_span('set_post_sent') as span:
        #     span.set_attribute('post_id', post_id)
        await self._redis.sadd('subscriber:e621:sent', post_id)

    async def get_last_post_version(self) -> int:
        # with tracer.start_as_current_span('get_last_post_version') as span:
        version = int(await self._redis.get('subscriber:e621:last_post_version') or '0')
        #     span.set_attribute('last_version', version)
        return version

    async def set_last_post_version(self, post_version: int):
        # with tracer.start_as_current_span('set_last_post_version') as span:
        #     span.set_attribute('post_version', post_version)
        await self._redis.set('subscriber:e621:last_post_version', post_version)

    async def dump(self) -> StorageDump:
        subs = await self.get_subs()
        sent = list(map(int, await self._redis.smembers('subscriber:e621:sent')))
        last_post_version = await self.get_last_post_version()

        return StorageDump(subs, sent, last_post_version)

    async def migrate(self):
        # with tracer.start_as_current_span('migrate'):
        current_version = int(await self._redis.get('subscriber:version') or '0')
        for migration in migrations[current_version:]:
            self._logger.info(f'Running migration {migration.__name__}')
            await migration(self._redis, self._logger)

        await self._redis.delete('subscriber:lock')


async def migration0_1(redis: Redis, logger: logging.Logger):
    async def rename(old: str, new: str):
        try:
            await redis.rename(old, new)
        except RedisError:
            logger.warning(f'Key `{old}` was not found')

    await rename('e621-go:subs', 'subscriber:e621:subs')
    await rename('e621-go:sent', 'subscriber:e621:sent')
    await rename('e621-go:last_post_version', 'subscriber:e621:last_post_version')
    await redis.set('subscriber:version', 1)

migrations = [
    migration0_1,
]
