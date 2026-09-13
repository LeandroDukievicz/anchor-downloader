import asyncio
from types import SimpleNamespace

import pytest
from telethon.errors import FloodPremiumWaitError

from tg_downloader.broker import BrokerClient, BrokerServer


class FakeTelegramClient:
    def __init__(self):
        self.get_me_calls = 0
        self.disconnected = False

    def is_connected(self):
        return True

    async def get_me(self):
        self.get_me_calls += 1
        return SimpleNamespace(first_name="Leandro", username="teste")

    async def disconnect(self):
        self.disconnected = True


@pytest.mark.asyncio
async def test_ten_dashboards_share_one_local_telegram_client(tmp_path):
    telegram = FakeTelegramClient()
    broker = BrokerServer(client=telegram)
    path = tmp_path / "broker.sock"
    server = await asyncio.start_unix_server(broker.handle, path=str(path))
    clients = [BrokerClient(path) for _ in range(10)]
    try:
        await asyncio.gather(*(client.connect() for client in clients))
        assert [client.me.first_name for client in clients] == ["Leandro"] * 10
        assert broker.client is telegram
        assert telegram.get_me_calls == 10
    finally:
        await asyncio.gather(*(client.disconnect() for client in clients))
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_global_transfer_limit_is_shared_between_windows():
    broker = BrokerServer(client=FakeTelegramClient(), global_limit=4)
    active = 0
    peak = 0

    async def operation():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return True

    await asyncio.gather(*(broker.network(operation) for _ in range(20)))
    assert peak == 4


@pytest.mark.asyncio
async def test_history_requests_do_not_bypass_global_limit():
    broker = BrokerServer(client=FakeTelegramClient(), global_limit=2)
    active = 0
    peak = 0

    async def operation():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    calls = [broker.network(operation, history=index % 2 == 0) for index in range(20)]
    await asyncio.gather(*calls)
    assert peak == 2


@pytest.mark.asyncio
async def test_premium_flood_wait_starts_one_shared_cooldown():
    broker = BrokerServer(client=FakeTelegramClient())

    async def throttled():
        raise FloodPremiumWaitError(request=None, capture=3)

    before = asyncio.get_running_loop().time()
    with pytest.raises(FloodPremiumWaitError):
        await broker.network(throttled)

    assert broker.cooldown_until >= before + 4
