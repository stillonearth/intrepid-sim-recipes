import asyncio

from functools import wraps
from centrifuge import Client, SubscriptionEventHandler, PublicationContext

TIMESTEP_MS = 300


def sync_wrap(func):
    """Decorator to run an async function synchronously."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


class WorldControllerBase:
    def __init__(self, dt_ms=3000):
        client = Client("ws://localhost:9120/connection/websocket")

        class EventHandler(SubscriptionEventHandler):
            async def on_publication(_, ctx: PublicationContext) -> None:
                self._last_tick_received = ctx.pub.data
                self._process_tick(ctx.pub.data)

        sync = client.new_subscription("sync", EventHandler())
        asyncio.ensure_future(sync.subscribe())

        self.client = client
        self._dt_ms = dt_ms
        self._last_tick_received = -1
        self._user_task = None

        fut = asyncio.ensure_future(client.connect())
        fut.add_done_callback(lambda _: asyncio.ensure_future(self.on_start()))

    async def connect(self):
        class EventHandler(SubscriptionEventHandler):
            async def on_publication(_, ctx: PublicationContext) -> None:
                if self.next_step:
                    self.next_step.set_result(ctx.pub.data)
                    self.next_step = None
                self.last_tick = ctx.pub.data

        next_step = self.next_step = asyncio.Future()
        sync = self.client.new_subscription("sync", EventHandler())
        await sync.subscribe()
        await self.client.connect()
        return await next_step

    async def on_start(self):
        # implemented by the user
        pass

    async def on_tick(self, _tick):
        commands = [
            self.rpc("session.step", None),
        ]
        await asyncio.gather(*commands)

    async def rpc(self, method, args):
        result = await self.client.rpc(method, args)
        return result.data

    def _process_tick(self, tick):
        if self._user_task and self._last_tick_received > 0:
            return  # busy

        # send sync
        next_tick = tick + self._dt_ms * 1_000
        sync = self.client.get_subscription("sync")
        asyncio.ensure_future(sync.publish(next_tick))

        def on_task_done(_):
            self._user_task = None
            if self._last_tick_received >= next_tick:
                self._process_tick(next_tick)

        self._user_task = asyncio.ensure_future(self.on_tick(tick))
        self._user_task.add_done_callback(on_task_done)

        # Session api calls

    async def simulation_step(self):
        return await self.rpc("session.step", None)

    async def simulation_restart(self):
        return await self.rpc("session.restart", None)

    async def simulation_time(self):
        state = await self.rpc("session.state", None)
        return state['time_us']
