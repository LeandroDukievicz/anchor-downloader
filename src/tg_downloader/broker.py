"""One Telegram connection per local account, shared by independent dashboards.

Private Unix socket, framed JSON/TL metadata and raw binary chunks. No pickle,
credentials on the wire, TCP listener or unbounded prefetch queue.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import inspect
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

from telethon.errors import FileReferenceExpiredError, FloodWaitError
from telethon.extensions import BinaryReader

from . import engine
from .diagnostics import logger

MAX_FRAME = 16 * 1024 * 1024
NETWORK_TIMEOUT = 60
PROTOCOL = 1


def socket_path() -> Path:
    return engine.BASE_DIR / "broker.sock"


def encode_tl(value):
    return base64.b64encode(bytes(value)).decode("ascii")


def decode_tl(value):
    with BinaryReader(base64.b64decode(value, validate=True)) as reader:
        return reader.tgread_object()


async def send_frame(writer, value):
    data = b"B" + value if isinstance(value, bytes) else b"J" + json.dumps(value).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Resposta local excedeu o tamanho permitido.")
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()


async def read_frame(reader):
    size, = struct.unpack("!I", await reader.readexactly(4))
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("Mensagem local invalida.")
    data = await reader.readexactly(size)
    if data[:1] == b"B":
        return data[1:]
    if data[:1] != b"J":
        raise ValueError("Protocolo local invalido.")
    return json.loads(data[1:])


async def close_writer(writer):
    writer.close()
    with contextlib.suppress(OSError, asyncio.TimeoutError):
        await asyncio.wait_for(writer.wait_closed(), 2)


async def _probe(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        await send_frame(writer, {"protocol": PROTOCOL, "method": "ping"})
        reply = await asyncio.wait_for(read_frame(reader), 2)
        if reply != {"ok": True}:
            raise RuntimeError("Resposta invalida do servico local.")
    finally:
        await close_writer(writer)


def _spawn_broker():
    process = subprocess.Popen(
        [sys.executable, "-m", "tg_downloader.broker"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    async def reap(child):
        while child.poll() is None:
            await asyncio.sleep(1)

    asyncio.create_task(reap(process))


async def ensure_broker(path: Path | None = None) -> None:
    path = path or socket_path()
    spawned = False
    for _ in range(100):
        try:
            await _probe(path)
            return
        except (OSError, RuntimeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            if not spawned:
                _spawn_broker()
                spawned = True
            await asyncio.sleep(0.1)
    raise RuntimeError("Servico local nao iniciou. Execute tg-downloader --doctor.")


class BrokerServer:
    def __init__(self, *, client=None, global_limit=4, idle_seconds=30):
        self.client = client
        self.limit = asyncio.Semaphore(global_limit)
        self.history_limit = asyncio.Semaphore(1)
        self.connect_lock = asyncio.Lock()
        self.idle_seconds = idle_seconds
        self.last_activity = time.monotonic()
        self.handlers = set()
        self.cooldown_until = 0.0
        self.server = None

    async def network(self, operation, *, history=False):
        # Share Telegram-imposed waits across windows. Fair semaphore acquisition
        # is per chunk, so a large file cannot occupy a slot for hours.
        async with self.history_limit if history else self.limit:
            delay = self.cooldown_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                return await asyncio.wait_for(operation(), NETWORK_TIMEOUT)
            except FloodWaitError as error:
                self.cooldown_until = max(self.cooldown_until, time.monotonic() + error.seconds)
                raise

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            request = await asyncio.wait_for(read_frame(reader), 10)
            if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
                raise RuntimeError("Versao do servico local incompativel. Feche as janelas antigas.")
            await self.dispatch(request, reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
            pass
        except Exception as error:
            # Do not propagate arbitrary Telegram errors containing credentials.
            payload = {"error": type(error).__name__, "message": "Falha no servico local/Telegram."}
            if isinstance(error, FloodWaitError):
                payload["seconds"] = error.seconds
            elif isinstance(error, FileReferenceExpiredError):
                payload["message"] = "A referencia temporaria da midia expirou."
            elif isinstance(error, (RuntimeError, ValueError)):
                payload["message"] = str(error)
            else:
                logger.exception("Falha inesperada no broker: %s", type(error).__name__)
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                await asyncio.wait_for(send_frame(writer, payload), 2)
        finally:
            await close_writer(writer)
            self.handlers.discard(task)
            self.last_activity = time.monotonic()

    async def dispatch(self, request, reader, writer):
        method = request.get("method")
        if method == "ping":
            await send_frame(writer, {"ok": True})
            return
        if method == "connect":
            async with self.connect_lock:
                if self.client is None:
                    if request.get("interactive"):
                        from .auth import connect

                        async def prompt(title, label, password=False):
                            await send_frame(writer, {"prompt": [title, label, password]})
                            reply = await asyncio.wait_for(read_frame(reader), 300)
                            return reply.get("value")

                        self.client, _ = await connect(prompt)
                    else:
                        self.client, _ = await engine.create_client()
                elif not self.client.is_connected():
                    await self.client.connect()
                try:
                    me = await self.network(self.client.get_me)
                    if me is None:
                        raise RuntimeError("Sessao nao autorizada. Conecte novamente em F3.")
                except BaseException:
                    await self.client.disconnect()
                    self.client = None
                    raise
                await send_frame(writer, {"user": {"first_name": me.first_name, "username": me.username}})
            return
        if self.client is None or not self.client.is_connected():
            async with self.connect_lock:
                if self.client is None:
                    self.client, _ = await engine.create_client()
                elif not self.client.is_connected():
                    await self.client.connect()
        if method == "resolve":
            chat = await self.network(lambda: engine.resolve_chat(self.client, request["link"]))
            await send_frame(writer, {"tl": encode_tl(chat)})
        elif method == "message":
            chat = decode_tl(request["chat"])
            message_id = int(request["message_id"])
            message = await self.network(
                lambda: self.client.get_messages(chat, ids=message_id), history=True,
            )
            if message is None:
                raise RuntimeError("A mensagem original nao esta mais disponivel.")
            await send_frame(writer, {"tl": encode_tl(message)})
        elif method in {"messages", "download"}:
            if method == "messages":
                stream = self.client.iter_messages(decode_tl(request["chat"]), reverse=True).__aiter__()
            else:
                offset = int(request["offset"])
                if offset < 0:
                    raise ValueError("Offset invalido.")
                stream = self.client.iter_download(
                    decode_tl(request["media"]), offset=offset,
                    request_size=engine.DOWNLOAD_CHUNK_SIZE,
                ).__aiter__()
            try:
                while await asyncio.wait_for(read_frame(reader), 3600) == {"next": True}:
                    try:
                        value = await self.network(lambda: anext(stream), history=method == "messages")
                    except StopAsyncIteration:
                        await send_frame(writer, {"end": True})
                        break
                    await send_frame(writer, bytes(value) if method == "download" else {"tl": encode_tl(value)})
            finally:
                close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
                if close:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
        else:
            raise ValueError("Comando local desconhecido.")

    async def serve(self, path):
        self.server = await asyncio.start_unix_server(self.handle, path=str(path))
        os.chmod(path, 0o600)
        try:
            async with self.server:
                while True:
                    await asyncio.sleep(1)
                    if not self.handlers and time.monotonic() - self.last_activity >= self.idle_seconds:
                        return
        finally:
            self.server.close()
            await self.server.wait_closed()
            for task in tuple(self.handlers):
                task.cancel()
            await asyncio.gather(*self.handlers, return_exceptions=True)
            if self.client:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.client.disconnect(), 10)
            Path(path).unlink(missing_ok=True)


class BrokerClient:
    def __init__(self, path=None):
        self.path = Path(path) if path else socket_path()
        self.writers = set()
        self.me = None
        self.heartbeat = None

    async def open(self, method, **payload):
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.path))
        except OSError:
            if self.path != socket_path():
                raise
            await ensure_broker(self.path)
            reader, writer = await asyncio.open_unix_connection(str(self.path))
        self.writers.add(writer)
        try:
            await send_frame(writer, dict(protocol=PROTOCOL, method=method, **payload))
        except BaseException:
            self.writers.discard(writer)
            await close_writer(writer)
            raise
        return reader, writer

    @staticmethod
    async def response(reader):
        value = await asyncio.wait_for(read_frame(reader), 3700)
        if isinstance(value, dict) and "error" in value:
            if value["error"] == "FloodWaitError":
                raise FloodWaitError(request=None, capture=int(value.get("seconds", 1)))
            if value["error"] == "FileReferenceExpiredError":
                raise FileReferenceExpiredError(request=None)
            if value["error"] in {"TimeoutError", "OSError", "ConnectionError"}:
                raise OSError("Conexao interrompida ou sem resposta; parcial preservado.")
            raise RuntimeError(f"{value['error']}: {value['message']}")
        return value

    async def finish(self, writer):
        self.writers.discard(writer)
        await close_writer(writer)

    async def call(self, method, **payload):
        reader, writer = await self.open(method, **payload)
        try:
            return await self.response(reader)
        finally:
            await self.finish(writer)

    async def connect(self, *, interactive=False, prompt=None):
        reader, writer = await self.open("connect", interactive=interactive)
        try:
            while True:
                reply = await self.response(reader)
                if "prompt" not in reply:
                    self.me = SimpleNamespace(**reply["user"])
                    break
                title, label, password = reply["prompt"]
                value = await prompt(title, label, password=password)
                await send_frame(writer, {"value": value})
        finally:
            await self.finish(writer)
        if not self.heartbeat:
            self.heartbeat = asyncio.create_task(self.keep_alive())

    async def keep_alive(self):
        while True:
            await asyncio.sleep(10)
            try:
                await asyncio.wait_for(self.call("ping"), 5)
            except (OSError, RuntimeError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                return

    async def get_me(self):
        return self.me

    async def resolve_link(self, link):
        return decode_tl((await self.call("resolve", link=link))["tl"])

    async def refresh_message(self, chat, message_id):
        reply = await self.call(
            "message", chat=encode_tl(chat), message_id=int(message_id),
        )
        return decode_tl(reply["tl"])

    async def iterate(self, method, **payload):
        reader, writer = await self.open(method, **payload)
        try:
            while True:
                await send_frame(writer, {"next": True})
                reply = await self.response(reader)
                if isinstance(reply, bytes):
                    yield reply
                elif reply.get("end"):
                    return
                else:
                    yield decode_tl(reply["tl"])
        finally:
            await self.finish(writer)

    def iter_messages(self, chat, *, reverse=True):
        return self.iterate("messages", chat=encode_tl(chat))

    def iter_download(self, message, *, offset, request_size):
        return self.iterate("download", media=encode_tl(message.media), offset=offset)

    async def disconnect(self):
        if self.heartbeat:
            self.heartbeat.cancel()
            await asyncio.gather(self.heartbeat, return_exceptions=True)
            self.heartbeat = None
        for writer in tuple(self.writers):
            await self.finish(writer)


async def shared_client(prompt=None):
    """Auto-start is serialized by an OS lock held for the broker's lifetime."""
    engine.BASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine.BASE_DIR.chmod(0o700)
    client = BrokerClient()
    await ensure_broker(client.path)
    try:
        await client.connect(interactive=prompt is not None, prompt=prompt)
        return client, engine.load_config()[2]
    except BaseException:
        await client.disconnect()
        raise


def main():
    engine.BASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine.BASE_DIR.chmod(0o700)
    with (engine.BASE_DIR / "broker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        logger.info("Servico compartilhado iniciado")
        path = socket_path()
        path.unlink(missing_ok=True)
        try:
            asyncio.run(BrokerServer().serve(path))
        finally:
            logger.info("Servico compartilhado finalizado")


if __name__ == "__main__":
    main()
