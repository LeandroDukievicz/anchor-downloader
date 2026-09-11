import asyncio
from types import SimpleNamespace

import pytest

from tg_downloader import engine


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
async def test_error_discards_partial_before_retrying_from_zero(tmp_path, monkeypatch):
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
        FailingStream([b"BAD!", OSError("falha simulada")]),
        FailingStream([b"GOOD", b"DATA", b"TEST"]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert client.offsets == [4, 0]
    assert (tmp_path / "arquivo.bin").read_bytes() == b"GOODDATATEST"
    assert not part_path.exists()
    assert item.status == "complete"
    assert manifest["7"]["42"]["status"] == "complete"


@pytest.mark.asyncio
async def test_failed_file_does_not_leave_partial_for_next_execution(tmp_path, monkeypatch):
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
        FailingStream([b"BAD!", OSError("falha simulada")]),
        FailingStream([b"BAD!", OSError("falha simulada")]),
        FailingStream([b"BAD!", OSError("falha simulada")]),
    ])

    await engine.process_single_download(
        0, client, item, state, tmp_path, manifest, asyncio.Lock(),
    )

    assert not part_path.exists()
    assert item.status == "error"
    assert manifest["7"]["42"]["status"] == "error"
