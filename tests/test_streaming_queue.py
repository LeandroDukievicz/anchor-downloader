from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageMediaPhoto, PhotoEmpty

from anchor_downloader import engine


def media_message(message_id):
    return SimpleNamespace(
        id=message_id,
        chat_id=99,
        media=MessageMediaPhoto(photo=PhotoEmpty(id=message_id)),
        file=SimpleNamespace(name=f"foto-{message_id}.jpg", size=4, mime_type="image/jpeg", ext=".jpg"),
        photo=object(),
        video=None,
        audio=None,
        voice=None,
        gif=None,
        sticker=None,
        document=None,
        raw_text="",
        message="",
    )


def test_sizing_volume_survives_dashboard_recomputation():
    state = engine.create_state(1, 1)
    state.update(
        phase="sizing",
        catalog_total_bytes=12_345,
        global_total_bytes=12_345,
    )

    engine.recompute_transfer_state(state)

    assert state["global_total_bytes"] == 12_345


def test_requested_concurrency_is_capped_at_global_transfer_limit():
    state = engine.create_state(1, 10)

    assert state["concurrency"] == engine.MAX_CONCURRENT_DOWNLOADS == 4


class StreamingClient:
    def __init__(self, state):
        self.state = state
        self.scan_passes = 0
        self.total_at_first_download = None

    async def iter_messages(self, chat, reverse=False):
        assert reverse is True
        self.scan_passes += 1
        yield media_message(1)
        yield media_message(2)

    def iter_download(self, message, *, offset, request_size):
        async def chunks():
            if self.total_at_first_download is None:
                self.total_at_first_download = (
                    self.state["catalog_complete"],
                    self.state["global_total_bytes"],
                    self.state["total_files"],
                )
            yield b"DATA"

        return chunks()


@pytest.mark.asyncio
async def test_exact_total_is_known_before_first_download(tmp_path):
    state = engine.create_state(1, 1)
    client = StreamingClient(state)
    chat = SimpleNamespace(id=99, title="Canal grande", username="", broadcast=True, megagroup=False)

    stats = await engine.download_messages(client, chat, tmp_path, state)

    assert client.scan_passes == 2
    assert client.total_at_first_download == (True, 8, 2)
    assert stats["downloaded"] == 2
    assert state["phase"] == "complete"
    assert (tmp_path / "Fotos" / "foto-1.jpg").read_bytes() == b"DATA"
    assert (tmp_path / "Fotos" / "foto-2.jpg").read_bytes() == b"DATA"
