import asyncio
import logging
import traceback
from enum import Enum
from functools import cached_property
from itertools import count
from random import shuffle

import httpx
from pydantic import BaseModel

from context import AppContext, tag_to_hashtag
from utils.media import convert_to_mp4, resize_image
from utils.telegram import send_as_document, send_as_photo, send_as_video
from utils.tracing import get_tracer, record_span_error, set_span_attributes, traced

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
    rating: str
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
    subs: list[str] | None = None

    @cached_property
    def tag_list(self):
        return self.tags.split(' ')

    def build_sub(self, subs: list[str]):
        tags = set(self.tag_list)
        matched_subs: list[str] = []
        for sub in subs:
            if ' ' in sub:
                sub_parts = sub.split(' ')
                matched = True
                for part in sub_parts:
                    if part.startswith('-'):
                        if part in tags:
                            matched = False
                    else:
                        if part not in tags:
                            matched = False
                if matched:
                    matched_subs.append(sub)
            else:
                if sub in tags:
                    matched_subs.append(sub)
        return matched_subs

    async def build_caption(self, ctx: AppContext):
        gelbooru = ctx.gelbooru
        tags = await gelbooru.get_tags(ctx, self.tag_list)

        caption_lines = []
        if self.subs:
            monitored_tags = set()
            for sub in self.subs:
                for tag in sub.split():
                    if tag.startswith('-'):
                        tag = f'-{tag_to_hashtag(tag[1:])}'
                    else:
                        tag = tag_to_hashtag(tag)
                    monitored_tags.add(tag)
            monitored_tags = sorted(monitored_tags)
            caption_lines += [
                f'Monitored tags: <b>{" ".join(monitored_tags)}</b>',
                'Matched queries:',
                *[f' - <code>{sub}</code>' for sub in self.subs],
            ]
        artist_tags = sorted(
            tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.ARTIST
        )
        character_tags = sorted(
            tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.CHARACTER
        )
        copyright_tags = sorted(
            tag.hashtag for tag in tags if tag.enum_type == GelbooruTagType.COPYRIGHT
        )
        if artist_tags:
            caption_lines.append(f'Artist: <b>{" ".join(artist_tags)}</b>')
        if character_tags:
            if len(character_tags) > 15:
                character_tags = character_tags[:15] + ['...']
            caption_lines.append(f'Character: <b>{" ".join(character_tags)}</b>')
        if copyright_tags:
            caption_lines.append(f'Copyright: <b>{" ".join(copyright_tags)}</b>')
        caption_lines += [
            '',
            f'https://gelbooru.com/index.php?page=post&s=view&id={self.id}',
        ]
        caption = '\n'.join(caption_lines)

        return caption

    @traced('gelbooru.send_post')
    async def send_post(self, ctx: AppContext):
        gelbooru = ctx.gelbooru

        set_span_attributes(
            {'gelbooru.post.id': self.id, 'gelbooru.subs': self.subs or ''}
        )

        if not self.file_url:
            logger.warning(f'file url is missing for post #{self.id}')
            return

        caption = await self.build_caption(ctx)

        logger.info(f'caption: {caption}')

        media_bytes = await gelbooru.download_media(self.file_url)
        ext = self.file_url.split('.')[-1]
        set_span_attributes(
            {'gelbooru.media.bytes': len(media_bytes), 'gelbooru.file.ext': ext}
        )
        if ext in ('jpg', 'png', 'webp'):
            media_bytes = await resize_image(media_bytes)
            set_span_attributes(
                {'gelbooru.send.via': 'photo', 'gelbooru.sent.bytes': len(media_bytes)}
            )
            await send_as_photo(ctx.bot, media_bytes, caption, f'g{self.id}')
        elif ext in ('gif', 'mp4', 'webm'):
            media_bytes = await convert_to_mp4(media_bytes)
            set_span_attributes(
                {'gelbooru.send.via': 'video', 'gelbooru.sent.bytes': len(media_bytes)}
            )
            await send_as_video(ctx.bot, media_bytes, caption, f'g{self.id}')
        elif ext in ('swf',):
            set_span_attributes(
                {
                    'gelbooru.send.via': 'document',
                    'gelbooru.sent.bytes': len(media_bytes),
                }
            )
            await send_as_document(ctx.bot, media_bytes, caption, f'g{self.id}', ext)
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
        self._client = httpx.AsyncClient(
            base_url='https://gelbooru.com',
            headers={'User-Agent': 'bot/py-3.0 (bakatrouble)'},
        )

    @traced('gelbooru.get_posts')
    async def get_posts(
        self,
        tags: str = '',
        page: int = 0,
        limit: int = 100,
        user_id: str = '',
        api_key: str = '',
    ) -> list[GelbooruPost]:
        set_span_attributes(
            {'gelbooru.tags': tags, 'gelbooru.page': page, 'gelbooru.limit': limit}
        )
        params = {
            'page': 'dapi',
            's': 'post',
            'q': 'index',
            'json': 1,
            'tags': tags,
            'limit': limit,
            'pid': page,
            'user_id': user_id,
            'api_key': api_key,
        }
        r = await self._client.get('/index.php', params=params)
        posts = [GelbooruPost.model_validate(p) for p in r.json()['post']]
        set_span_attributes({'gelbooru.posts.count': len(posts)})
        return posts

    @traced('gelbooru.get_tags')
    async def get_tags(self, ctx: AppContext, tags: list[str]) -> list[GelbooruTag]:
        set_span_attributes({'gelbooru.tags.count': len(tags)})
        if not ctx.config.gelbooru:
            raise RuntimeError('gelbooru not configured')
        fetched_tags = []
        for page_num in count():
            params = {
                'page': 'dapi',
                's': 'tag',
                'q': 'index',
                'json': 1,
                'names': ' '.join(tags),
                'pid': page_num,
                'limit': 100,
                'user_id': ctx.config.gelbooru.user_id,
                'api_key': ctx.config.gelbooru.api_key,
            }
            r = await self._client.get('/index.php', params=params)
            fetched_tags += [GelbooruTag.model_validate(p) for p in r.json()['tag']]
            if len(r.json()['tag']) < 100:
                break
        set_span_attributes({'gelbooru.tags.fetched': len(fetched_tags)})
        return fetched_tags

    @traced('gelbooru.download_media')
    async def download_media(self, url: str) -> bytes:
        r = await self._client.get(url, headers={'referer': 'https://gelbooru.com/'})
        set_span_attributes({'gelbooru.media.bytes': len(r.content)})
        return r.content

    @traced('gelbooru.process_new_posts')
    async def process_new_posts(self, ctx: AppContext):
        storage = ctx.storage
        if not ctx.config.gelbooru:
            raise RuntimeError('gelbooru not configured')

        async with storage.gelbooru.lock:
            tracer = get_tracer('websites.gelbooru')

            logger.info('lock acquired')
            page_size = 100
            posts_to_post: list[GelbooruPost] = []

            subs = await storage.gelbooru.get_subs()
            with tracer.start_as_current_span('gelbooru.process_new_subs') as span:
                span.set_attribute('gelbooru.subs.count', len(subs))
                for sub in subs:
                    if not await storage.gelbooru.get_scanned(sub):
                        posts = await self.get_posts(
                            tags=sub,
                            page=0,
                            limit=page_size,
                            user_id=ctx.config.gelbooru.user_id,
                            api_key=ctx.config.gelbooru.api_key,
                        )
                        for post in posts:
                            await storage.gelbooru.set_post_sent(post.id)
                        await storage.gelbooru.set_scanned(sub)

            chunks: list[list[str]] = []
            partial_chunk: list[str] = []
            shuffle(subs)
            for sub in subs:
                if ' ' in sub:
                    chunks.append([sub])
                else:
                    partial_chunk.append(sub)
                    if len(' ~ '.join(partial_chunk)) > 400:
                        chunks.append(partial_chunk)
                        partial_chunk = []
            if partial_chunk:
                chunks.append(partial_chunk)

            with tracer.start_as_current_span('gelbooru.fetch_posts') as span:
                for chunk in chunks:
                    new_posts = []
                    logger.info(f'fetching posts for `{"`, `".join(chunk)}`')
                    for page_num in count():
                        # OR format = `{tag1 ~ tag2}`
                        page = await self.get_posts(
                            tags=f'{{{" ~ ".join(chunk)}}}',
                            page=page_num,
                            limit=page_size,
                        )
                        sent_flags = await storage.gelbooru.get_post_sent(
                            [p.id for p in page]
                        )
                        final_page = False
                        for post in page:
                            if not sent_flags[post.id]:
                                post.subs = post.build_sub(subs)
                                new_posts.append(post)
                            else:
                                final_page = True

                        logger.info(
                            f'page {page_num} loaded, count={len(page)}, new_posts={len(new_posts)}'
                        )
                        if final_page or len(page) < page_size:
                            break
                    posts_to_post.extend(new_posts)

            posts_to_post.sort(key=lambda p: p.id)
            sent_flags = await storage.gelbooru.get_post_sent(
                [p.id for p in posts_to_post]
            )
            logger.info(f'sent_flags={sent_flags}')
            posts_to_post = [p for p in posts_to_post if not sent_flags[p.id]]
            set_span_attributes(
                {
                    'gelbooru.chunks.count': len(chunks),
                    'gelbooru.posts.unsent': len(posts_to_post),
                }
            )

            if not posts_to_post:
                logger.info('no unsent posts')
                return

            with tracer.start_as_current_span('gelbooru.send_posts') as span:
                span.set_attribute('gelbooru.posts.count', len(posts_to_post))
                for post in posts_to_post:
                    with tracer.start_as_current_span('gelbooru.post') as span:
                        span.set_attribute('gelbooru.post.id', post.id)
                        span.set_attribute('gelbooru.subs', post.subs or '')
                        try:
                            if not sent_flags[post.id]:
                                await post.send_post(ctx)
                                await storage.gelbooru.set_post_sent(post.id)
                                sent_flags[post.id] = True
                                span.set_attribute('gelbooru.post.sent', True)
                            else:
                                span.set_attribute('gelbooru.post.sent', False)
                        except Exception as e:  # noqa: BLE001
                            record_span_error(span, e)
                            logger.error(traceback.format_exception(e))
                    await asyncio.sleep(3)
            set_span_attributes(
                {'gelbooru.posts.sent': sum(1 for v in sent_flags.values() if v)}
            )

    async def worker(self, ctx: AppContext):
        tracer = get_tracer('websites.gelbooru')
        if not ctx.config.gelbooru:
            raise RuntimeError('gelbooru not configured')
        while True:
            with tracer.start_as_current_span('gelbooru.worker_tick'):
                try:
                    await self.process_new_posts(ctx)
                except Exception as e:  # noqa: BLE001
                    logger.error(traceback.format_exception(e))
            await asyncio.sleep(ctx.config.gelbooru.interval.total_seconds())
