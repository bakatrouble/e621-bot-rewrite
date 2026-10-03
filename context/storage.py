import logging
from dataclasses import dataclass

from redis import RedisError
from redis.asyncio import Redis


__all__ = ['Storage', 'StorageDump', 'migrations']


@dataclass
class StorageDump:
    subs: list[str]
    sent: list[int]
    last_post_version: int


class StorageImpl:
    def __init__(self, redis: Redis, logger: logging.Logger, name: str):
        self._redis = redis
        self._name = name
        self.lock = redis.lock(f'subscriber:{self._name}:lock')
        self._logger = logger

    async def get_subs(self) -> list[str]:
        return [str(sub) for sub in sorted(await self._redis.smembers(f'subscriber:{self._name}:subs'))]

    async def add_sub(self, sub: str):
        await self._redis.sadd(f'subscriber:{self._name}:subs', sub)

    async def remove_sub(self, sub: str):
        await self._redis.srem(f'subscriber:{self._name}:subs', sub)

    async def get_post_sent(self, post_ids: list[int]) -> dict[int, bool]:
        if not post_ids:
            return {}
        return {post_id: bool(ismember)
                for post_id, ismember
                in zip(post_ids, await self._redis.smismember(f'subscriber:{self._name}:sent', post_ids))}

    async def set_post_sent(self, post_id: int):
        await self._redis.sadd(f'subscriber:{self._name}:sent', post_id)

    async def get_last_post_version(self) -> int:
        version = int(await self._redis.get(f'subscriber:{self._name}:last_post_version') or '0')
        return version

    async def set_last_post_version(self, post_version: int):
        await self._redis.set(f'subscriber:{self._name}:last_post_version', post_version)

    async def get_scanned(self, key: str) -> bool:
        return await self._redis.hexists(f'subscriber:{self._name}:scanned', key)

    async def set_scanned(self, key: str):
        await self._redis.hset(f'subscriber:{self._name}:scanned', key, 1)

    async def dump(self) -> StorageDump:
        subs = await self.get_subs()
        sent = list(map(int, await self._redis.smembers(f'subscriber:{self._name}:sent')))
        last_post_version = await self.get_last_post_version()

        return StorageDump(subs, sent, last_post_version)


class Storage:
    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self.lock = self._redis.lock('subscriber:lock')
        self._logger = logging.getLogger('storage')

        self.e621 = StorageImpl(self._redis, self._logger, 'e621')
        self.gelbooru = StorageImpl(self._redis, self._logger, 'gelbooru')

    async def migrate(self):
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
