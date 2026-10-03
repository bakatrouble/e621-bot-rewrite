import asyncio
from functools import wraps

import hypercorn.asyncio
from hypercorn import Config
from sanic import Sanic, Request, json
from sanic.exceptions import BadURL
from sanic_ext import Extend

from context import config, storage as storage_root


app = Sanic('subscriber')
app.config.CORS_ORIGINS = '*'
Extend(app)


async def start():
    conf = Config()
    conf.bind = config.api.bind
    await hypercorn.asyncio.serve(app, conf)


def protected():
    def decorator(f):
        @wraps(f)
        async def decorated_function(request: Request, *args, **kwargs):
            if request.headers.get('x-api-key') not in config.api.keys:
                return json({'status': 'error', 'message': 'Forbidden'}, 403)
            return await f(request, *args, **kwargs)
        return decorated_function
    return decorator


def get_storage(request: Request):
    website = request.args.get('website', 'e621')
    match website:
        case 'e621':
            return storage_root.e621
        case 'gelbooru':
            return storage_root.gelbooru
        case _:
            raise BadURL('invalid website')


@app.get('/api/subscriptions')
@protected()
async def subscriptions_get(request: Request):
    storage = get_storage(request)
    subs = await storage.get_subs()
    return json({'status': 'success', 'subscriptions': subs})


@app.post('/api/subscriptions')
@protected()
async def subscriptions_post(request: Request):
    storage = get_storage(request)
    subs: list[str] = request.json.get('subs', [])
    if not subs:
        return json({'status': 'error', 'message': 'No subs provided'}, 400)

    existing_subs = set(await storage.get_subs())
    subs = list(map(lambda s: s.lower(), subs))
    conflicts = [sub for sub in subs if sub in existing_subs]

    if conflicts:
        return json({'status': 'error', 'message': 'Some subscriptions already exist', 'conflicts': conflicts}, 409)

    for sub in subs:
        await storage.add_sub(sub)

    subs.sort()
    return json({'status': 'ok', 'added': subs})


@app.delete('/api/subscriptions')
@protected()
async def subscriptions_delete(request: Request):
    storage = get_storage(request)
    subs: list[str] = request.json.get('subs', [])

    existing_subs = set(await storage.get_subs())
    subs = list(map(lambda s: s.lower(), subs))
    missing = [sub for sub in subs if sub not in existing_subs]

    if missing:
        return json({'status': 'error', 'message': 'Some subscriptions do not exist', 'missing': missing}, 404)

    for sub in subs:
        await storage.remove_sub(sub)

    subs.sort()
    return json({'status': 'ok', 'deleted': subs})


if __name__ == '__main__':
    asyncio.run(start())
