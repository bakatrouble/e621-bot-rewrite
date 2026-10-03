import asyncio
import logging
import traceback
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from itertools import count
from typing import Literal

import httpx
from pydantic import BaseModel

from context import storage, config, tag_to_hashtag, bot
from utils.media import resize_image, convert_to_mp4
from utils.telegram import send_as_photo, send_as_video, send_as_document

logger = logging.getLogger('gelbooru')


class GelbooruPost(BaseModel):
    id: int
    created_at: str
    score: int
    width: int
    height: int
    md5: str
    directory: str
    image: str
    rating: Literal['general'] | Literal['sensitive'] | Literal['explicit']
    change: int
    owner: str
    creator_id: int
    parent_id: int
    sample: int
    preview_height: int
    preview_width: int
    tags: str
    title: str
    has_notes: str
    has_comments: str
    file_url: str
    preview_url: str
    sample_url: str
    sample_height: int
    sample_width: int
    status: str
    post_locked: int
    has_children: str
    sub: str | None = None

    @cached_property
    def tag_list(self):
        return self.tags.split(' ')

    async def send_post(self):
        from websites import gelbooru

        if not self.file_url:
            logger.warning(f'file url is missing for post #{self.id}')
            return

        tags = await gelbooru.get_tags(self.tag_list)

        caption_lines = []
        if self.sub is not None:
            matched_tags = [tag.hashtag for tag in tags if tag.name in self.sub]
            caption_lines.append(f'Matched tags: <b>{' '.join(matched_tags)}</b>')
            caption_lines.append(f'Subscription: <code>{self.sub}</code>')
        artist_tags = list(sorted(tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.ARTIST))
        character_tags = list(sorted(tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.CHARACTER))
        copyright_tags = list(sorted(tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.COPYRIGHT))
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
            f'https://gelbooru.com/index.php?page=post&s=view&id={self.id}'
        ]
        caption = '\n'.join(caption_lines)
        logger.info(f'caption: {caption}')

        media_bytes = gelbooru.download_media(self.file_url)
        ext = self.file_url.split('.')[-1]
        if ext in ('jpg', 'png', 'webp'):
            media_bytes = await resize_image(media_bytes)
            await send_as_photo(bot, media_bytes, caption, f'g{self.id}')
        elif ext in ('gif', 'mp4', 'webm'):
            media_bytes = await convert_to_mp4(media_bytes)
            await send_as_video(bot, media_bytes, caption, f'g{self.id}')
        elif ext in ('swf',):
            await send_as_document(bot, media_bytes, caption, f'g{self.id}', ext)
        else:
            raise RuntimeError(f'unsupported file type: {ext}')


class GelbooruTagType(Enum):
    GENERAL = 0
    ARTIST = 1
    COPYRIGHT = 3
    CHARACTER = 4
    META = 5
    DEPRECATED = 6
    UNKNOWN = -1


class GelbooruTag(BaseModel):
    id: int
    name: str
    count: int
    type: int
    ambiguous: int

    @property
    def enum_type(self):
        try:
            return GelbooruTagType(self.type)
        except ValueError:
            return GelbooruTagType.UNKNOWN

    @property
    def hashtag(self):
        return tag_to_hashtag(self.name)


class Gelbooru:
    def __init__(self):
        self._client = httpx.AsyncClient(base_url='https://gelbooru.com',
                                         headers={'User-Agent': 'bot/py-3.0 (bakatrouble)'})

    async def get_posts(self,
                        tags: str = '',
                        page: int = 0,
                        limit: int = 100):
        params = {
            'page': 'dapi',
            's': 'post',
            'q': 'index',
            'json': 1,
            'tags': tags,
            'limit': limit,
            'pid': page,
            'user_id': config.gelbooru.user_id,
            'api_key': config.gelbooru.api_key,
        }
        r = await self._client.get(f'/index.php', params=params)
        return [GelbooruPost.model_validate(p) for p in r.json()['post']]

    async def get_tags(self, tags: list[str]) -> list[GelbooruTag]:
        fetched_tags = []
        for page_num in count():
            params = {
                'page': 'dapi',
                's': 'tag',
                'names': ' '.join(tags),
                'pid': page_num,
                'limit': 100,
                'user_id': config.gelbooru.user_id,
                'api_key': config.gelbooru.api_key,
            }
            r = await self._client.get(f'/index.php', params=params)
            fetched_tags += [GelbooruTag.model_validate(p) for p in r.json()['tag']]
            if len(r.json()['tag']) < 100:
                break
        return fetched_tags

    async def download_media(self, url: str) -> bytes:
        r = await self._client.get(url)
        return r.content

    async def process_new_posts(self):
        async with storage.gelbooru.lock:
            logger.info('lock acquired')
            page_size = 100
            posts_to_post: list[GelbooruPost] = []
            for sub in await storage.gelbooru.get_subs():
                new_posts = []
                scanned = await storage.gelbooru.get_scanned()
                logging.info(f'fetching posts for `{sub}`')
                for page_num in count():
                    page = await self.get_posts(tags=sub, page=page_num, limit=page_size)
                    sent_flags = await storage.gelbooru.get_post_sent([p.id for p in page])
                    final_page = not scanned
                    for post in page:
                        if not sent_flags[post.id]:
                            post.sub = sub
                            new_posts.append(post)
                        else:
                            final_page = True

                    logger.info(f'page {page_num} loaded, count={len(page)}, new_posts={len(new_posts)}')
                    if final_page or len(page) < page_size:
                        break
                if not scanned:
                    for post in new_posts:
                        await storage.gelbooru.set_post_sent(post.id)
                    await storage.gelbooru.set_scanned(sub)
                    continue
                posts_to_post.extend(new_posts)

            posts_to_post.sort(key=lambda p: p.id)
            sent_flags = await storage.gelbooru.get_post_sent([p.id for p in posts_to_post])
            logging.info(f'sent_flags={sent_flags}')
            posts_to_post = [p for p in posts_to_post if not sent_flags[p.id]]

            if not posts_to_post:
                logger.info(f'no unsent posts')
                return

            for post in posts_to_post:
                try:
                    if not sent_flags[post.id]:
                        await post.send_post()
                        await storage.gelbooru.set_post_sent(post.id)
                        sent_flags[post.id] = True
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
