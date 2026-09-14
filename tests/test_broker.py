import asyncio
import gc
import weakref
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from telethon.errors import FloodPremiumWaitError
from telethon.tl.types import InputPeerChannel

from tg_downloader.broker import (
    BrokerClient,
    BrokerServer,
    release_exception_memory,
    release_unused_memory,
)


class FakeTelegramClient:
    def __init__(self):
        self.get_me_calls = 0
        self.disconnected = False
        self.connect_calls = 0

    def is_connected(self):
        return True

    async def get_me(self):
        self.get_me_calls += 1
        return SimpleNamespace(first_name="Leandro", username="teste")

    async def disconnect(self):
        self.disconnected = True

    async def connect(self):
        self.connect_calls += 1


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


@pytest.mark.asyncio
async def test_repeated_network_timeouts_renew_the_telegram_transport(monkeypatch):
    telegram = FakeTelegramClient()
    broker = BrokerServer(client=telegram)

    async def stalled():
        raise asyncio.TimeoutError()

    monkeypatch.setattr("tg_downloader.broker.TIMEOUTS_BEFORE_CONNECTION_RESET", 2)
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await broker.network(stalled)

    assert telegram.disconnected is True
    assert telegram.connect_calls == 1
    assert broker.consecutive_timeouts == 0


def test_release_exception_memory_breaks_nested_traceback_chains():
    retained = bytearray(512 * 1024)
    try:
        try:
            raise OSError("temporary")
        except OSError as cause:
            raise RuntimeError("wrapper") from cause
    except RuntimeError as error:
        cause = error.__cause__
        assert cause is not None
        assert error.__traceback__ is not None
        assert cause.__traceback__ is not None

        release_exception_memory(error)

        assert error.__traceback__ is None
        assert error.__cause__ is None
        assert error.__context__ is None
        assert cause.__traceback__ is None
    assert len(retained) == 512 * 1024


def test_release_unused_memory_always_runs_full_gc():
    with patch.object(gc, "collect") as collect:
        release_unused_memory()
    collect.assert_called_once_with()


@pytest.mark.asyncio
async def test_traceback_release_drops_a_future_with_a_large_result():
    class Payload:
        def __init__(self):
            self.data = bytes(512 * 1024)

    references = []

    async def fail_after_response():
        payload = Payload()
        references.append(weakref.ref(payload))
        response = asyncio.get_running_loop().create_future()
        response.set_result(payload)
        raise TimeoutError("request stalled")

    try:
        await fail_after_response()
    except TimeoutError as error:
        assert references[0]() is not None
        release_exception_memory(error)

    gc.collect()
    assert references[0]() is None


@pytest.mark.asyncio
async def test_history_stream_resumes_after_local_connection_reset(tmp_path, monkeypatch):
    client = BrokerClient(tmp_path / "broker.sock")
    calls = []

    async def fake_iterate(method, **payload):
        calls.append((method, payload["min_id"]))
        if len(calls) == 1:
            yield SimpleNamespace(id=10)
            raise ConnectionResetError("socket reiniciado")
        # A duplicate is filtered defensively even though a real broker uses
        # min_id and would normally start directly at message 11.
        yield SimpleNamespace(id=10)
        yield SimpleNamespace(id=11)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(client, "iterate", fake_iterate)
    monkeypatch.setattr(asyncio, "sleep", no_wait)

    messages = [
        message.id
        async for message in client.iter_messages(
            InputPeerChannel(channel_id=99, access_hash=0),
            reverse=True,
        )
    ]

    assert messages == [10, 11]
    assert calls == [("messages", 0), ("messages", 10)]
