import asyncio
import logging
import traceback
from dataclasses import dataclass
from typing import Optional, Iterable

import httpx
from pydantic import BaseModel

from context import storage, config, tag_to_hashtag, bot
from context.query import Query
from utils.media import resize_image, convert_to_mp4
from utils.telegram import send_as_photo, send_as_video, send_as_document

logger = logging.getLogger('e621')


class E621PostFile(BaseModel):
    width: int
    height: int
    ext: str
    size: int
    md5: str
    url: Optional[str]


class E621PostTags(BaseModel):
    general: list[str]
    species: list[str]
    character: list[str]
    copyright: list[str]
    artist: list[str]
    invalid: list[str]
    lore: list[str]
    meta: list[str]


class E621Post(BaseModel):
    id: int
    created_at: str
    updated_at: str
    file: E621PostFile
    tags: E621PostTags

    @property
    def flat_tags(self) -> list[str]:
        return (self.tags.general +
                self.tags.species +
                self.tags.character +
                self.tags.copyright +
                self.tags.artist +
                self.tags.invalid +
                self.tags.lore +
                self.tags.meta)

    async def send_post(self, matched_queries: list[Query] | None = None):
        from websites import e621

        if not self.file.url:
            logger.warning(f'file url is missing for post #{self.id}')
            return

        caption_lines = []
        if matched_queries is not None:
            matched_tags = set()
            for q in matched_queries:
                matched_tags |= q.mentioned_tags()
            monitored_tags = list(sorted([tag_to_hashtag(tag) for tag in self.flat_tags if tag in matched_tags]))
            caption_lines += [
                f'Monitored tags: <b>{' '.join(monitored_tags)}</b>',
                f'Matched queries:',
                *[f' - <code>{match}</code>' for match in matched_queries],
            ]
        artist_tags = list(sorted([tag_to_hashtag(tag) for tag in self.tags.artist]))
        character_tags = list(sorted([tag_to_hashtag(tag) for tag in self.tags.character]))
        copyright_tags = list(sorted([tag_to_hashtag(tag) for tag in self.tags.copyright]))
        if artist_tags:
            caption_lines.append(f'Artist: <b>{' '.join(artist_tags)}</b>')
        if character_tags:
            if len(character_tags) > 15:
                character_tags = character_tags[:15] + ['...']
            caption_lines.append(f'Character: <b>{' '.join(character_tags)}</b>')
        if copyright_tags:
            caption_lines.append(f'Copyright: <b>{' '.join(copyright_tags)}</b>')
        caption_lines += [
            '',
            f'https://e621.net/posts/{self.id}'
        ]
        caption = '\n'.join(caption_lines)
        logger.info(f'caption: {caption}')

        media_bytes = await e621.download_media(self.file.url)
        if self.file.ext in ('jpg', 'png', 'webp'):
            media_bytes = await resize_image(media_bytes)
            await send_as_photo(bot, media_bytes, caption, f'e{self.id}')
        elif self.file.ext in ('gif', 'mp4', 'webm'):
            media_bytes = await convert_to_mp4(media_bytes)
            await send_as_video(bot, media_bytes, caption, f'e{self.id}')
        elif self.file.ext in ('swf',):
            await send_as_document(bot, media_bytes, caption, f'e{self.id}', self.file.ext)
        else:
            raise RuntimeError(f'unsupported file type: {self.file.ext}')


class E621PostVersion(BaseModel):
    id: int
    post_id: int
    tags: str
    added_tags: list[str]
    removed_tags: list[str]

    def check_queries(self, queries: Iterable[Query]) -> list[Query]:
        matched_queries = []
        current_tags = set(self.tags.split())
        prev_tags = (current_tags | set(self.removed_tags)) - set(self.added_tags)
        for query in queries:
            if query.check(current_tags) and not query.check(prev_tags):
                matched_queries.append(query)
        return matched_queries


class E621TagAlias(BaseModel):
    status: str
    antecedent_name: str
    consequent_name: str


@dataclass
class E621MatchedPV:
    matched_queries: list[Query]
    post_version: E621PostVersion

    async def send_post(self):
        from websites import e621
        post = await e621.get_post(self.post_version.post_id)
        await post.send_post(self.matched_queries)


class E621:
    def __init__(self):
        self._client = httpx.AsyncClient(base_url='https://e621.net',
                                         headers={'User-Agent': 'bot/py-3.0 (bakatrouble)'})

    async def get_post(self, post_id: int) -> E621Post:
        r = await self._client.get(f'/posts/{post_id}.json')
        post = E621Post.model_validate(r.json()['post'])
        if post.file.url is None:
            md5 = post.file.md5
            post.file.url = f'https://static1.e621.net/data/{md5[0:2]}/{md5[2:4]}/{md5}.{post.file.ext}'
        return post

    async def get_post_versions(self,
                                after_id: int | None = None,
                                before_id: int | None = None,
                                post_id: int | None = None,
                                limit: int = 320) -> list[E621PostVersion]:
        params = {'limit': str(limit)}
        if before_id is not None:
            params['page'] = f'b{before_id}'
        elif after_id is not None:
            params['page'] = f'a{after_id}'

        if post_id is not None:
            params['search[post_id]'] = str(post_id)

        r = await self._client.get(f'/post_versions.json', params=params)
        if '<title>e621 Maintenance</title>' in r.text:
            return []
        r = r.json()
        if isinstance(r, dict) and r.get('success') is not None:
            return []

        return [E621PostVersion.model_validate(pv) for pv in r]

    async def get_posts(self,
                        tags: str = '',
                        page: int = 1,
                        limit: int = 320) -> list[E621Post]:
        params = {'tags': tags, 'page': str(page), 'limit': str(limit)}
        r = await self._client.get(f'/posts.json', params=params)
        return [E621Post.model_validate(p) for p in r.json()]

    async def get_tag_aliases(self, tag: str) -> list[str]:
        params = {'search[name_matches]': tag}
        r = await self._client.get(f'/tag_aliases.json', params=params)
        r = r.json()
        tag_aliases = [E621TagAlias.model_validate(ta) for ta in r]
        return [ta.consequent_name for ta in tag_aliases if ta.status == 'active']

    async def download_media(self, url: str) -> bytes:
        r = await self._client.get(url)
        return r.content

    async def process_new_posts(self):
        logger.info('processing new posts')
        async with storage.e621.lock:
            logger.info('lock acquired')
            queries = Query.get_queries(await storage.e621.get_subs())
            last_post_version = await storage.e621.get_last_post_version()
            page_size = 320
            pvs_to_post: list[E621MatchedPV] = []
            for i in range(10):
                after_id = last_post_version or None
                page = await self.get_post_versions(after_id=after_id, limit=page_size)

                page.reverse()
                for post_version in page:
                    if post_version.id > last_post_version:
                        last_post_version = post_version.id
                    if matched_queries := post_version.check_queries(queries):
                        pvs_to_post.append(E621MatchedPV(matched_queries, post_version))

                logger.info(f'page {i} loaded, count={len(page)}, matched={len(pvs_to_post)}')

                if len(page) < page_size:
                    break

            pvs_to_post.sort(key=lambda plan: plan.post_version.id)
            sent_flags = await storage.e621.get_post_sent([plan.post_version.post_id for plan in pvs_to_post])
            logging.info(f'sent_flags: {sent_flags}')
            pvs_to_post = [plan for plan in pvs_to_post if not sent_flags[plan.post_version.post_id]]
            logger.info(f'unsent posts: {len(pvs_to_post)}')

            if not pvs_to_post:
                logger.info(f'no unsent posts')
                await storage.e621.set_last_post_version(last_post_version)
                return

            for plan in pvs_to_post:
                try:
                    if not sent_flags[plan.post_version.post_id]:
                        await plan.send_post()
                        await storage.e621.set_post_sent(plan.post_version.post_id)
                        sent_flags[plan.post_version.post_id] = True
                    await storage.e621.set_last_post_version(plan.post_version.id)
                except Exception as e:
                    logger.error(traceback.format_exception(e))
                finally:
                    await asyncio.sleep(3)

    async def worker(self):
        while True:
            try:
                await self.process_new_posts()
            except Exception as e:
                logger.error(traceback.format_exception(e))
            finally:
                await asyncio.sleep(config.interval.total_seconds())
