import asyncio
from types import SimpleNamespace

import pytest
from telethon.errors import FileReferenceExpiredError, FloodPremiumWaitError
from telethon.tl.types import MessageMediaPhoto, PhotoEmpty

from anchor_downloader import engine


class FailingStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = next(self.chunks)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    async def aclose(self):
        pass


class FakeClient:
    def __init__(self, streams):
        self.streams = iter(streams)
        self.offsets = []

    def iter_download(self, message, *, offset, request_size):
        self.offsets.append(offset)
        return next(self.streams)


def make_download(tmp_path, expected_size):
    message = SimpleNamespace(id=42, chat_id=7)
    chat_info = {"id": "7", "title": "Teste", "reference": "", "link": ""}
    item = engine.Download(
        1, message, "arquivo.bin", tmp_path / "arquivo.bin", "Documentos",
        expected_size, "", chat_info,
    )
    item.log_entry = engine._make_log_entry(item)
    return item


@pytest.mark.asyncio
async def test_network_error_resumes_partial_from_last_complete_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DOWNLOAD_CHUNK_SIZE", 4)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_SECONDS", 0)

    item = make_download(tmp_path, expected_size=12)
    part_path = tmp_path / "arquivo.bin.part"
    part_path.write_bytes(b"OLD!")
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([
        FailingStream([b"DATA", OSError("falha simulada")]),
        FailingStream([b"TAIL"]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert client.offsets == [4, 8]
    assert (tmp_path / "arquivo.bin").read_bytes() == b"OLD!DATATAIL"
    assert not part_path.exists()
    assert item.status == "complete"
    assert manifest["7"]["42"]["status"] == "complete"
    assert item.message is None


@pytest.mark.asyncio
async def test_unknown_remote_error_never_discards_a_valid_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DOWNLOAD_CHUNK_SIZE", 4)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_SECONDS", 0)

    item = make_download(tmp_path, expected_size=8)
    part_path = tmp_path / "arquivo.bin.part"
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([
        FailingStream([b"HEAD", RuntimeError("erro remoto desconhecido")]),
        FailingStream([b"TAIL"]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert client.offsets == [0, 4]
    assert (tmp_path / "arquivo.bin").read_bytes() == b"HEADTAIL"
    assert not part_path.exists()
    assert item.status == "complete"


@pytest.mark.asyncio
async def test_premium_flood_wait_preserves_partial_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DOWNLOAD_CHUNK_SIZE", 4)

    waits = []

    async def no_wait(seconds, item, state):
        waits.append(seconds)

    monkeypatch.setattr(engine, "_wait_retry", no_wait)
    item = make_download(tmp_path, expected_size=8)
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([
        FailingStream([b"HEAD", FloodPremiumWaitError(request=None, capture=3)]),
        FailingStream([b"TAIL"]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert waits == [4]
    assert client.offsets == [0, 4]
    assert (tmp_path / "arquivo.bin").read_bytes() == b"HEADTAIL"
    assert item.status == "complete"


@pytest.mark.asyncio
async def test_exhausted_network_retries_preserve_partial_for_next_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DOWNLOAD_CHUNK_SIZE", 4)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(engine, "MAX_TRANSIENT_RETRIES", 2)

    item = make_download(tmp_path, expected_size=12)
    part_path = tmp_path / "arquivo.bin.part"
    part_path.write_bytes(b"OLD!")
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([
        FailingStream([OSError("falha simulada")]),
        FailingStream([OSError("falha simulada")]),
        FailingStream([OSError("falha simulada")]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert part_path.read_bytes() == b"OLD!"
    assert item.status == "error"
    assert manifest["7"]["42"]["status"] == "error"


@pytest.mark.asyncio
async def test_expired_file_reference_is_refreshed_without_losing_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DOWNLOAD_CHUNK_SIZE", 4)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_SECONDS", 0)

    item = make_download(tmp_path, expected_size=12)
    part_path = tmp_path / "arquivo.bin.part"
    part_path.write_bytes(b"HEAD")
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}

    class RefreshingClient(FakeClient):
        def __init__(self):
            super().__init__([
                FailingStream([b"MID1", FileReferenceExpiredError(request=None)]),
                FailingStream([b"TAIL"]),
            ])
            self.refreshes = 0

        async def get_messages(self, chat, ids):
            self.refreshes += 1
            return SimpleNamespace(
                id=ids, chat_id=7,
                media=MessageMediaPhoto(photo=PhotoEmpty(id=ids)),
            )

    client = RefreshingClient()
    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
        chat=SimpleNamespace(id=7),
    )

    assert client.refreshes == 1
    assert client.offsets == [4, 8]
    assert (tmp_path / "arquivo.bin").read_bytes() == b"HEADMID1TAIL"
    assert item.status == "complete"


@pytest.mark.asyncio
async def test_chunk_watchdog_turns_a_stall_into_a_visible_error(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CHUNK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(engine, "MAX_TRANSIENT_RETRIES", 1)
    monkeypatch.setattr(engine, "RETRY_BACKOFF_SECONDS", 0)

    class StalledStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Future()

        async def aclose(self):
            pass

    item = make_download(tmp_path, expected_size=4)
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([StalledStream(), StalledStream()])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert item.status == "error"
    assert "sem progresso" in item.error
    assert client.offsets == [0, 0]


@pytest.mark.asyncio
async def test_missing_destination_is_reported_as_storage_failure(tmp_path):
    item = make_download(tmp_path, expected_size=4)
    item.target = tmp_path / "unmounted" / "arquivo.bin"
    state = engine.create_state(1, 1)
    state["downloads"].append(item)
    state["total_files"] = 1
    manifest = {"7": {"42": {"relpath": "arquivo.bin", "status": "pending"}}}
    client = FakeClient([FailingStream([b"DATA"])])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert item.status == "error"
    assert "armazenamento" in item.error
    assert "montado" in item.error
