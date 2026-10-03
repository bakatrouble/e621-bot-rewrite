import asyncio
import logging
from io import BytesIO

from aiogram import Dispatcher
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InaccessibleMessage
from httpx import AsyncClient

from context import bot, config
from utils.cache import is_cached, cache_file


class SendCallback(CallbackData, prefix='send'):
    destination: str
    filename: str


class UnsendCallback(CallbackData, prefix='unsend'):
    destination: str
    upload_id: str
    filename: str


class TelegramBot:
    def __init__(self):
        logging.basicConfig(level=logging.INFO)
        self._dispatcher = Dispatcher()
        self._dispatcher.callback_query.register(self.send_handler, SendCallback.filter())
        self._dispatcher.callback_query.register(self.unsend_handler, UnsendCallback.filter())

    async def start(self):
        await self._dispatcher.start_polling(bot, handle_signals=False)

    async def stop(self):
        await self._dispatcher.stop_polling()

    async def send_handler(self, query: CallbackQuery, callback_data: SendCallback):
        message = query.message
        if isinstance(message, InaccessibleMessage) or not message:
            logging.warning(f'unable to access message')
            await query.answer()
            return

        if callback_data.destination == 'nsfw':
            api_base = config.destinations.nsfw
        elif callback_data.destination == 'sfw':
            api_base = config.destinations.sfw
        else:
            raise RuntimeError(f'Destination {callback_data.destination} is not supported')

        cached_name = callback_data.filename

        cache_path, exists = is_cached(cached_name)
        if not exists:
            if query.message.photo:
                file_id = query.message.photo[-1].file_id
            elif query.message.document:
                file_id = query.message.document.file_id
            elif query.message.video:
                file_id = query.message.video.file_id
            else:
                logging.warning(f'no media')
                await query.answer()
                return

            out = BytesIO()
            await bot.download(file_id, out)
            media = out.getvalue()
            cache_path = cache_file(media, cached_name)

        async with AsyncClient() as client:
            r = await client.post(f'{api_base}/internalSend', json={'path': str(cache_path)})
            r = r.json()
        if r['status'] == 'ok':
            await query.answer('Sent')
        elif r['status'] == 'duplicate':
            await query.answer('Duplicate')
        else:
            await query.answer('Error')

        if r['status'] == 'ok' and message.reply_markup:
            kbd = message.reply_markup
            if callback_data.destination == 'nsfw':
                kbd.inline_keyboard[0][0].text = 'Cancel NSFW'
                kbd.inline_keyboard[0][0].callback_data = UnsendCallback(destination='nsfw',
                                                                         upload_id=r['upload_id'],
                                                                         filename=cached_name).pack()
            elif callback_data.destination == 'sfw':
                kbd.inline_keyboard[0][1].text = 'Cancel SFW'
                kbd.inline_keyboard[0][1].callback_data = UnsendCallback(destination='sfw',
                                                                         upload_id=r['upload_id'],
                                                                         filename=cached_name).pack()
            await message.edit_reply_markup(reply_markup=kbd)

    async def unsend_handler(self, query: CallbackQuery, callback_data: UnsendCallback):
        message = query.message
        if isinstance(message, InaccessibleMessage) or not message:
            logging.warning(f'unable to access message')
            await query.answer()
            return

        if callback_data.destination == 'nsfw':
            api_base = config.destinations.nsfw
        elif callback_data.destination == 'sfw':
            api_base = config.destinations.sfw
        else:
            raise RuntimeError(f'Destination {callback_data.destination} is not supported')

        async with AsyncClient() as client:
            r = await client.delete(f'{api_base}/internalDelete', json={'upload_id': callback_data.upload_id})
            r = r.json()
        if r['status'] == 'ok':
            await query.answer('Unsent')
        else:
            await query.answer('Error')

        if r['status'] == 'ok' and message.reply_markup:
            kbd = message.reply_markup
            if callback_data.destination == 'nsfw':
                kbd.inline_keyboard[0][0].text = 'NSFW'
                kbd.inline_keyboard[0][0].callback_data = SendCallback(destination='nsfw',
                                                                       filename=callback_data.filename).pack()
            elif callback_data.destination == 'sfw':
                kbd.inline_keyboard[0][1].text = 'SFW'
                kbd.inline_keyboard[0][1].callback_data = SendCallback(destination='sfw',
                                                                       filename=callback_data.filename).pack()
            await message.edit_reply_markup(reply_markup=kbd)


telegram_bot = TelegramBot()


if __name__ == '__main__':
    asyncio.run(telegram_bot.start())
