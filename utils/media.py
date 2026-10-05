import json
import logging
from io import BytesIO
from tempfile import TemporaryDirectory

import magic
from PIL import Image
from PIL.Image import Resampling
from ffmpeg.asyncio import FFmpeg

from utils.tracing import set_span_attributes, traced


@traced('media.convert_to_mp4')
async def convert_to_mp4(media: bytes) -> bytes:
    mime = magic.from_buffer(media, mime=True)
    set_span_attributes({'media.input_bytes': len(media), 'media.mime': mime})

    if mime == 'video/mp4':
        ext = 'mp4'
    elif mime == 'image/gif':
        ext = 'gif'
    elif mime == 'video/webm' or mime == 'application/octet-stream':
        ext = 'webm'
    else:
        raise RuntimeError(f'Unsupported media type: {mime}')

    with TemporaryDirectory() as d:
        with open(f'{d}/input.{ext}', 'wb') as f:
            f.write(media)
            f.close()

        ffmpeg = FFmpeg()\
            .option('hide_banner')\
            .option('y')\
            .input(f'{d}/input.{ext}')\
            .output(
                f'{d}/output.mp4',
                {'c:v': 'libx264', 'c:a': 'aac', 'b:a': '128k'},
                vf='pad=width=ceil(iw/2)*2:height=ceil(ih/2)*2:x=0:y=0:color=black',
                crf=26,
                movflags='+faststart',
            )
        await ffmpeg.execute()

        with open(f'{d}/output.mp4', 'rb') as f:
            out = f.read()
        set_span_attributes({'media.output_bytes': len(out)})
        return out


@traced('media.resize_image')
async def resize_image(media: bytes) -> bytes:
    set_span_attributes({'media.input_bytes': len(media)})
    src_im = Image.open(BytesIO(media))
    src_im.load()

    if src_im.mode != 'RGB':
        im = Image.new('RGB', src_im.size, (255, 255, 255))
        channels = src_im.split()
        im.paste(src_im, mask=channels[3] if len(channels) == 4 else None)
    else:
        im = src_im

    width, height = im.size
    set_span_attributes({'media.width': width, 'media.height': height})
    if width + height > 10000:
        scale = 10000. / (width + height)
        width = int(width * scale)
        height = int(height * scale)
        im = im.resize((width, height), Resampling.LANCZOS)
        logging.info(f'resized image to {width}x{height}')

    out = BytesIO()
    while True:
        im.save(out, format='JPEG', quality=95)
        buf_size = out.tell()
        if buf_size > 10*1024*1024:
            out.truncate(0)
            width = int(width * .95)
            height = int(height * .95)
            im = im.resize((width, height), Resampling.LANCZOS)
            logging.info(f'buf is {buf_size} bytes, resized to {width}x{height}')
        else:
            break

    result = out.getvalue()
    set_span_attributes({'media.output_bytes': len(result)})
    return result


async def mp4_has_audio(media: bytes) -> bool:
    with TemporaryDirectory() as d:
        with open(f'{d}/input.mp4', 'wb') as f:
            f.write(media)

        ffprobe = FFmpeg(executable='ffprobe')\
            .input(f'{d}/input.mp4', print_format='json', show_streams=None)
        result = json.loads(await ffprobe.execute())
        return any(stream['codec_type'] == 'audio' for stream in result['streams'])
