from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel


__all__ = ['Config']


class DestinationsConfig(BaseModel):
    nsfw: str
    sfw: str


class APIConfig(BaseModel):
    bind: str
    keys: list[str]


class Config(BaseModel):
    bot_token: str
    chat_id: int
    api: APIConfig | None = None
    interval: timedelta
    redis: str
    cache_dir: Path = Path('cache')
    destinations: DestinationsConfig | None = None
    production: bool | None = False

    @classmethod
    def load(cls, path: str) -> 'Config':
        with open(path, 'r') as f:
            config = Config.model_validate(yaml.load(f, yaml.Loader))
        config.cache_dir = config.cache_dir.absolute()
        config.cache_dir.mkdir(exist_ok=True, parents=True)
        return config
