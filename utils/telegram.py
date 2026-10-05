from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import SendCallback, UnsendCallback
from context import config
from utils.tracing import traced


def build_keyboard(filename: str, nsfw_upload_id: str | None = None, sfw_upload_id: str | None = None):
    if nsfw_upload_id:
        nsfw_callback_data = UnsendCallback(destination='nsfw', upload_id=nsfw_upload_id, filename=filename)
    else:
        nsfw_callback_data = SendCallback(destination='nsfw', filename=filename)

    if sfw_upload_id:
        sfw_callback_data = UnsendCallback(destination='sfw', upload_id=sfw_upload_id, filename=filename)
    else:
        sfw_callback_data = SendCallback(destination='sfw', filename=filename)

    return InlineKeyboardBuilder()\
        .button(text='Cancel NSFW' if nsfw_upload_id else 'NSFW',
                style='primary',
                callback_data=nsfw_callback_data)\
        .button(text='Cancel SFW' if sfw_upload_id else 'SFW',
                style='success',
                callback_data=sfw_callback_data)\
        .as_markup()


@traced('telegram.send_as_photo')
async def send_as_photo(bot: Bot, media: bytes, caption: str, post_id: str):
    cached_name = f'{post_id}.jpg'
    kb = build_keyboard(cached_name)
    await bot.send_photo(config.chat_id,
                         BufferedInputFile(media, f'{post_id}.jpg'),
                         reply_markup=kb,
                         caption=caption,
                         parse_mode='html')


@traced('telegram.send_as_video')
async def send_as_video(bot: Bot, media: bytes, caption: str, post_id: str):
    if len(media) < 50*1024*1024:
        cached_name = f'{post_id}.mp4'
        kb = build_keyboard(cached_name)
        await bot.send_video(config.chat_id,
                             BufferedInputFile(media, f'{post_id}.mp4'),
                             reply_markup=kb,
                             caption=caption,
                             supports_streaming=True,
                             parse_mode='html')
    else:
        await bot.send_message(config.chat_id,
                               caption,
                               parse_mode='html')


@traced('telegram.send_as_document')
async def send_as_document(bot: Bot, media: bytes, caption: str, post_id: str, ext: str):
    if len(media) < 50*1024*1024:
        await bot.send_document(config.chat_id,
                                BufferedInputFile(media, f'{post_id}.{ext}'),
                                caption=caption,
                                parse_mode='html')
    else:
        await bot.send_message(config.chat_id,
                               caption,
                               parse_mode='html')
