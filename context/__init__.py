import re

from aiogram import Bot

from .config import Config
from .storage import Storage


__all__ = ['config', 'storage', 'tag_to_hashtag', 'bot']


def tag_to_hashtag(tag: str) -> str:
    return f'#{re.sub(r'[^a-zA-Z0-9_]', '_', tag)}'


config = Config.load('config.yaml')
storage = Storage(config.redis)
bot = Bot(config.bot_token)
