import asyncio
from functools import wraps

import hypercorn.asyncio
from hypercorn import Config
from sanic import Config as SanicConfig
from sanic import Request, Sanic, json
from sanic.exceptions import BadURL
from sanic_ext import Extend

from context import AppContext
from utils.tracing import instrument_sanic, set_span_attributes, traced

app: Sanic = Sanic[SanicConfig, AppContext]('subscriber')
app.config.CORS_ORIGINS = '*'
Extend(app)
instrument_sanic(app)


async def start(ctx: AppContext):
    if not ctx.config.api:
        raise RuntimeError('API config is not provided')

    from utils.tracing import setup_tracing

    setup_tracing('e621-bot-api')
    app.ctx = ctx
    conf = Config()
    conf.bind = ctx.config.api.bind
    await hypercorn.asyncio.serve(app, conf)


def protected():
    def decorator(f):
        @wraps(f)
        async def decorated_function(request: Request, *args, **kwargs):
            if request.headers.get('x-api-key') not in app.ctx.config.api.keys:
                return json({'status': 'error', 'message': 'Forbidden'}, 403)
            return await f(request, *args, **kwargs)

        return decorated_function

    return decorator


def get_storage(request: Request):
    website = request.args.get('website', 'e621')
    match website:
        case 'e621':
            return app.ctx.storage.e621
        case 'gelbooru':
            return app.ctx.storage.gelbooru
        case _:
            raise BadURL('invalid website')


@app.get('/api/subscriptions')
@protected()
@traced('api.subscriptions_get')
async def subscriptions_get(request: Request):
    storage = get_storage(request)
    set_span_attributes({'api.website': request.args.get('website', 'e621')})
    subs = await storage.get_subs()
    set_span_attributes({'api.subs.count': len(subs)})
    return json({'status': 'success', 'subscriptions': subs})


@app.post('/api/subscriptions')
@protected()
@traced('api.subscriptions_post')
async def subscriptions_post(request: Request):
    storage = get_storage(request)
    set_span_attributes({'api.website': request.args.get('website', 'e621')})
    subs: list[str] = request.json.get('subs', [])
    set_span_attributes({'api.subs.requested': len(subs)})
    if not subs:
        return json({'status': 'error', 'message': 'No subs provided'}, 400)

    existing_subs = set(await storage.get_subs())
    subs = [s.lower() for s in subs]
    conflicts = [sub for sub in subs if sub in existing_subs]

    if conflicts:
        set_span_attributes({'api.subs.conflicts': len(conflicts)})
        return json(
            {
                'status': 'error',
                'message': 'Some subscriptions already exist',
                'conflicts': conflicts,
            },
            409,
        )

    for sub in subs:
        await storage.add_sub(sub)

    subs.sort()
    set_span_attributes({'api.subs.added': len(subs)})
    return json({'status': 'ok', 'added': subs})


@app.delete('/api/subscriptions')
@protected()
@traced('api.subscriptions_delete')
async def subscriptions_delete(request: Request):
    storage = get_storage(request)
    set_span_attributes({'api.website': request.args.get('website', 'e621')})
    subs: list[str] = request.json.get('subs', [])
    set_span_attributes({'api.subs.requested': len(subs)})

    existing_subs = set(await storage.get_subs())
    subs = [s.lower() for s in subs]
    missing = [sub for sub in subs if sub not in existing_subs]

    if missing:
        set_span_attributes({'api.subs.missing': len(missing)})
        return json(
            {
                'status': 'error',
                'message': 'Some subscriptions do not exist',
                'missing': missing,
            },
            404,
        )

    for sub in subs:
        await storage.remove_sub(sub)

    subs.sort()
    set_span_attributes({'api.subs.deleted': len(subs)})
    return json({'status': 'ok', 'deleted': subs})


if __name__ == '__main__':
    asyncio.run(start(AppContext()))
