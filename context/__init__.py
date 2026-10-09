import re

from aiogram import Bot

__all__ = ['AppContext', 'tag_to_hashtag']


def tag_to_hashtag(tag: str) -> str:
    return f'#{re.sub(r"[^a-zA-Z0-9_]", "_", tag)}'


class AppContext:
    def __init__(self):
        from websites.e621 import E621
        from websites.gelbooru import Gelbooru

        from .config import Config
        from .storage import Storage

        self.config = Config.load('config.yaml')
        self.storage = Storage(self.config.redis)
        self.bot = Bot(self.config.bot_token)
        self.e621 = E621()
        self.gelbooru = Gelbooru()
