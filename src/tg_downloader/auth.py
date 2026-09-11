"""Interactive Telegram authentication without writing credentials to logs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
import os
from pathlib import Path
import tempfile

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .engine import STRING_SESSION_FILE, load_config


class AuthCancelled(RuntimeError):
    """The user cancelled an authentication prompt."""


def _save_session(value: str) -> None:
    STRING_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=STRING_SESSION_FILE.parent,
            prefix=".session-", delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STRING_SESSION_FILE)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


async def connect(
    prompt: Callable[..., Awaitable[str | None]],
) -> tuple[TelegramClient, int]:
    """Connect an existing session or authenticate with async UI prompts.

    The callback receives (title, label, password=False). None cancels the
    operation and raises AuthCancelled. Failed or cancelled clients are closed;
    the caller owns the connected, authorized client returned on success.
    """
    api_id, api_hash, concurrency = load_config()
    session_string = (
        STRING_SESSION_FILE.read_text(encoding="utf-8").strip()
        if STRING_SESSION_FILE.exists() else ""
    )
    client = TelegramClient(
        StringSession(session_string), api_id, api_hash,
        sequential_updates=False,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            phone = await prompt("CONECTAR AO TELEGRAM", "Telefone com codigo do pais (ex.: +5511999999999)")
            if phone is None:
                raise AuthCancelled("Conexao cancelada.")
            phone = phone.strip()
            if not phone:
                raise ValueError("O telefone nao pode ficar vazio.")
            sent = await client.send_code_request(phone)
            code = await prompt("CODIGO DO TELEGRAM", "Codigo de verificacao recebido no Telegram ou SMS")
            if code is None:
                raise AuthCancelled("Conexao cancelada.")
            code = "".join(code.split())
            if not code:
                raise ValueError("O codigo nao pode ficar vazio.")
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                password = await prompt("VERIFICACAO EM DUAS ETAPAS", "Senha de verificacao em duas etapas", password=True)
                if password is None:
                    raise AuthCancelled("Conexao cancelada.")
                await client.sign_in(password=password)
        if not await client.is_user_authorized():
            raise RuntimeError("O Telegram nao autorizou esta sessao.")
        saved_session = client.session.save()
        if not saved_session:
            raise RuntimeError("O Telegram retornou uma sessao vazia.")
        _save_session(saved_session)
        return client, concurrency
    except BaseException:
        with suppress(Exception):
            await client.disconnect()
        raise
