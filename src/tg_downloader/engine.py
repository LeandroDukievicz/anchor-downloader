"""Telegram transfer engine extracted from the user's v6.1 downloader."""

import asyncio
import contextlib
import fcntl
import inspect
import json
import mimetypes
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    FileReferenceExpiredError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    RPCError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (
    ChatInviteAlready,
    DocumentAttributeAudio,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from .diagnostics import logger


BASE_DIR = Path(
    os.environ.get(
        "TG_DOWNLOADER_HOME",
        Path.home() / ".config" / "telegram-downloader",
    )
).expanduser().resolve()
CONFIG_FILE = BASE_DIR / "config.json"
STRING_SESSION_FILE = BASE_DIR / "sessions" / "session_string"
INSTANCE_DIR = BASE_DIR / "instances"
MAX_SLOT_SEARCH = 999
MAX_DOWNLOAD_RETRIES = 3
MAX_TRANSIENT_RETRIES = 12
MAX_FILE_REFERENCE_REFRESHES = 3
RETRY_BACKOFF_SECONDS = 2
MAX_RETRY_BACKOFF_SECONDS = 60
CHUNK_TIMEOUT_SECONDS = 90
DEFAULT_CONCURRENT_DOWNLOADS = 4
DOWNLOAD_CHUNK_SIZE = 512 * 1024
SPEED_HISTORY_LEN = 120
STATE_FILE_MAX_AGE = 8
LOG_MAX_ENTRIES = 200
MANIFEST_FILENAME = ".telegram_downloader_manifest.json"
LOG_FILENAME = "telegram_downloader_log.txt"
CATEGORIES = dict.fromkeys(("Fotos", "Videos", "Musicas", "Audios", "Documentos", "Legendas", "GIFs", "Stickers", "Outros"), {})
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
TOPIC_PATTERN = re.compile(r"^(\d{1,3})[\.\-_\s]+(.+)$")
GENERIC_STEM_PATTERN = re.compile(
    r"^(img|image|video|vid|audio|voice|file|arquivo|doc|document|telegram|photo)[\s_\-]*\d*$",
    re.IGNORECASE,
)
HASH_LIKE_PATTERN = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
TERMINAL_STATUSES = {"complete", "skipped", "error", "removed"}


def load_config():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"Configuracao nao encontrada: {CONFIG_FILE}") from None
    except (OSError, ValueError):
        raise RuntimeError("Nao foi possivel ler config.json.") from None
    if not isinstance(config, dict):
        raise RuntimeError("config.json deve conter um objeto JSON.")
    try:
        api_id = int(config.get("api_id", 0))
    except (ValueError, TypeError):
        raise RuntimeError("api_id invalido em config.json.") from None
    api_hash = config.get("api_hash")
    if api_id <= 0 or not isinstance(api_hash, str) or not api_hash.strip():
        raise RuntimeError("Preencha api_id e api_hash em config.json.")
    try:
        concurrency = max(1, min(10, int(config.get("concurrent_downloads", DEFAULT_CONCURRENT_DOWNLOADS))))
    except (ValueError, TypeError):
        concurrency = DEFAULT_CONCURRENT_DOWNLOADS
    return api_id, api_hash.strip(), concurrency


def load_string_session():
    try:
        session = STRING_SESSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError(f"Sessao nao encontrada ou inacessivel: {STRING_SESSION_FILE}") from None
    if not session:
        raise RuntimeError("A StringSession esta vazia.")
    return session


async def create_client():
    api_id, api_hash, concurrency = load_config()
    try:
        session = StringSession(load_string_session())
    except (ValueError, TypeError):
        raise RuntimeError("A StringSession salva e invalida.") from None
    client = TelegramClient(
        session, api_id, api_hash, sequential_updates=False,
        receive_updates=False, flood_sleep_threshold=0,
        request_retries=3, connection_retries=3,
        raise_last_call_error=True,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("A sessao salva nao esta autenticada. Configure uma nova sessao.")
    except BaseException:
        await client.disconnect()
        raise
    return client, concurrency


def acquire_slot():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for number in range(1, MAX_SLOT_SEARCH + 1):
        handle = (INSTANCE_DIR / f"slot_{number}.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        except BaseException:
            handle.close()
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return number, handle
    raise RuntimeError("Nenhum slot de instancia disponivel.")


def instance_state_path(slot):
    return INSTANCE_DIR / f"state_slot_{slot}.json"


def publish_instance_state(slot, payload):
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = dict(payload, slot=slot, ts=time.time(), pid=os.getpid())
    path = instance_state_path(slot)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink()


def read_active_instances():
    instances = []
    now = time.time()
    for path in INSTANCE_DIR.glob("state_slot_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or now - float(data.get("ts", 0)) > STATE_FILE_MAX_AGE:
                continue
            pid = data.get("pid")
            if pid:
                os.kill(int(pid), 0)
            data["slot"] = int(data["slot"])
            instances.append(data)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return sorted(instances, key=lambda item: item["slot"])


@contextlib.contextmanager
def destination_lock(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    handle = (destination / ".tg-downloader.lock").open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Esta pasta ja esta sendo usada por outra instancia do TG Downloader.") from None
        yield
    finally:
        handle.close()


def add_log(state, level, text):
    diagnostic_level = {
        "ERR": logger.error,
        "WARN": logger.warning,
        "OK": logger.info,
        "INFO": logger.info,
    }.get(level, logger.info)
    diagnostic_level(str(text))
    state["log"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "text": str(text),
        "color": {"OK": "#32f6a3", "WARN": "#e6eb59", "ERR": "#ff548c"}.get(level, "#00dcff"),
    })


def add_recent(state, index, name, status, marker="+"):
    state["recent"].append({"index": index, "name": name, "status": status, "marker": marker})


def create_state(slot, concurrency):
    return {
        "slot": slot, "concurrency": max(1, min(10, int(concurrency))),
        "connected": False, "account": "Usuario", "username": "",
        "chat_title": "", "chat_username": "", "destination": "",
        "phase_label": "PRONTO", "phase": "startup", "total_files": 0,
        "catalog_total_files": 0, "catalog_total_bytes": 0, "catalog_complete": False,
        "discovered_files": 0,
        "completed": 0, "skipped": 0, "errors": 0, "removed": 0,
        "global_bytes": 0, "global_total_bytes": 0, "_completed_bytes": 0,
        "speed": 0.0, "peak_speed": 0.0, "eta": None,
        "speed_history": deque(maxlen=SPEED_HISTORY_LEN),
        "active": {}, "downloads": [], "pending_count": 0, "pending_preview": [],
        "categories": {name: 0 for name in CATEGORIES},
        "log": deque(maxlen=LOG_MAX_ENTRIES), "selected": 0,
        "expanded_log": False, "stop": False, "render_enabled": False,
        "last_broadcast": 0, "incomplete": 0,
        "recent": deque(maxlen=8), "log_entries": [], "log_path": "",
        "status_counts": {status: 0 for status in TERMINAL_STATUSES | {"queued", "paused", "downloading"}},
    }


class Download:
    __slots__ = (
        "index", "message", "message_id", "filename", "target", "category",
        "expected_size", "message_link", "chat_info", "current_bytes", "speed",
        "eta", "status", "removed", "resume_event", "completed_at",
        "original_name", "error", "log_entry",
    )

    def __init__(self, index, message, filename, target, category, expected_size, message_link, chat_info):
        self.index = index
        self.message = message
        self.message_id = int(message.id)
        self.filename = filename
        self.target = Path(target)
        self.category = category
        self.expected_size = max(0, int(expected_size or 0))
        self.message_link = message_link
        self.chat_info = chat_info
        self.current_bytes = 0
        self.speed = 0.0
        self.eta = None
        self.status = "queued"
        self.removed = False
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.completed_at = None
        self.original_name = get_attribute_filename(message) or filename
        self.error = ""
        self.log_entry = None


class DownloadRemoved(Exception):
    pass


class DownloadStopped(Exception):
    pass


class DownloadIntegrityError(OSError):
    pass


class DownloadStorageError(OSError):
    pass


def extract_invite_hash(link):
    match = re.search(r"(?:t|telegram)\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", link, re.IGNORECASE)
    return match.group(1) if match else None


async def resolve_chat(client, link):
    if hasattr(client, "resolve_link"):
        return await client.resolve_link(link)
    link = link.strip()
    invite_hash = extract_invite_hash(link)
    if invite_hash:
        try:
            invite = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(invite, ChatInviteAlready):
                return invite.chat
            result = await client(ImportChatInviteRequest(invite_hash))
            chats = getattr(result, "chats", None)
            if chats:
                return chats[0]
            raise RuntimeError("O convite foi processado, mas o Telegram nao retornou um grupo.")
        except InviteHashExpiredError:
            raise RuntimeError("O convite privado expirou ou foi revogado.") from None
        except InviteHashInvalidError:
            raise RuntimeError("O convite privado e invalido.") from None
        except RPCError as error:
            raise RuntimeError(f"O Telegram recusou o convite ({type(error).__name__}).") from None
    normalized = link if "://" in link else "https://" + link
    parsed = urlparse(normalized)
    if parsed.hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        pieces = parsed.path.strip("/").split("/")
        if pieces[0] == "c" and len(pieces) > 1 and pieces[1].isdigit():
            reference = int("-100" + pieces[1])
        else:
            reference = pieces[1] if pieces[0] == "s" and len(pieces) > 1 else pieces[0]
    else:
        reference = link.lstrip("@")
        if re.fullmatch(r"-?\d+", reference):
            reference = int(reference)
        elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", reference):
            raise RuntimeError("Informe @usuario, link t.me ou ID numerico do chat.")
    try:
        return await client.get_entity(reference)
    except (ValueError, RPCError) as error:
        raise RuntimeError(f"Nao foi possivel acessar o chat ({type(error).__name__}).") from None


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(name or "arquivo"))
    return name.strip().rstrip(".") or "arquivo"


def slugify_text(text, max_len=90):
    if not text:
        return ""
    cleaned = sanitize_filename(re.sub(r"\s+", " ", str(text)).strip())[:max_len].rstrip()
    return cleaned.encode("utf-8")[:180].decode("utf-8", errors="ignore")


def message_has_media(message):
    # A webpage preview can expose ``message.file`` and ``message.photo`` but
    # cannot be passed to Telegram's upload.getFile. Only Telegram media with
    # a real document/photo location belongs in the download queue.
    return isinstance(getattr(message, "media", None), (MessageMediaDocument, MessageMediaPhoto))


def get_message_link(chat, message_id):
    username = (getattr(chat, "username", None) or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(getattr(chat, "id", ""))
    if chat_id.startswith("-100"):
        chat_id = chat_id[4:]
    if chat_id.isdigit() and (getattr(chat, "broadcast", False) or getattr(chat, "megagroup", False)):
        return f"https://t.me/c/{chat_id}/{message_id}"
    return ""


def is_generic_stem(stem):
    if not stem:
        return True
    normalized = stem.strip()
    if not normalized:
        return True
    if GENERIC_STEM_PATTERN.fullmatch(normalized):
        return True
    if HASH_LIKE_PATTERN.fullmatch(normalized):
        return True
    if normalized.isdigit():
        return True
    return False


def get_attribute_filename(message):
    file_obj = getattr(message, "file", None)
    if not file_obj:
        return None
    name = getattr(file_obj, "name", None)
    return sanitize_filename(name) if name else None


def get_audio_label(message):
    document = getattr(message, "document", None)
    attributes = getattr(document, "attributes", None) or []
    for attribute in attributes:
        if isinstance(attribute, DocumentAttributeAudio):
            performer = (getattr(attribute, "performer", None) or "").strip()
            title = (getattr(attribute, "title", None) or "").strip()
            if performer and title:
                return f"{performer} - {title}"
            return title or performer or None
    return None


def extract_caption_text(message):
    text = (getattr(message, "raw_text", None) or getattr(message, "message", None) or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    return first_line or None


def guess_extension(message, attr_name=None):
    if attr_name:
        ext = Path(attr_name).suffix
        if ext:
            return ext

    file_obj = getattr(message, "file", None)
    if file_obj:
        ext = getattr(file_obj, "ext", None)
        if ext:
            return ext
        mime = getattr(file_obj, "mime_type", None)
        if mime:
            guessed = mimetypes.guess_extension(mime)
            if guessed:
                return guessed

    if getattr(message, "photo", None):
        return ".jpg"
    if getattr(message, "video", None):
        return ".mp4"
    if getattr(message, "audio", None):
        return ".mp3"
    if getattr(message, "voice", None):
        return ".ogg"
    return ""


def detect_topic(attr_name):
    if not attr_name:
        return None, None
    stem = Path(attr_name).stem
    match = TOPIC_PATTERN.match(stem)
    if not match:
        return None, None
    return match.group(1), (match.group(2).strip(" .-_") or None)


def build_filename_parts(message):
    attr_name = get_attribute_filename(message)
    ext = guess_extension(message, attr_name)
    topic_number, topic_rest = detect_topic(attr_name)

    candidates = []
    caption = extract_caption_text(message)
    if caption:
        candidates.append(caption)
    audio_label = get_audio_label(message)
    if audio_label:
        candidates.append(audio_label)
    if topic_rest:
        candidates.append(topic_rest)
    if attr_name:
        attr_stem = Path(attr_name).stem
        if not is_generic_stem(attr_stem):
            candidates.append(attr_stem)

    stem = None
    for candidate in candidates:
        cleaned = slugify_text(candidate)
        if cleaned:
            stem = cleaned
            break

    if not stem:
        stem = f"telegram_{message.id}"

    if topic_number and not stem.startswith(topic_number):
        stem = f"{topic_number}.{stem}"

    return stem, ext, topic_number


def get_category(message):
    file_obj = getattr(message, "file", None)

    if getattr(message, "photo", None):
        return "Fotos"
    if getattr(message, "video", None):
        return "Videos"
    if getattr(message, "audio", None):
        return "Musicas"
    if getattr(message, "voice", None):
        return "Audios"
    if getattr(message, "gif", None):
        return "GIFs"
    if getattr(message, "sticker", None):
        return "Stickers"

    if file_obj:
        mime = (getattr(file_obj, "mime_type", None) or "").lower()
        name = (getattr(file_obj, "name", None) or "").lower()
        ext = Path(name).suffix.lower()

        if ext in SUBTITLE_EXTENSIONS or mime in ("application/x-subrip", "text/vtt"):
            return "Legendas"
        if mime.startswith("image/"):
            return "GIFs" if (mime == "image/gif" or ext == ".gif") else "Fotos"
        if mime.startswith("video/"):
            return "Videos"
        if mime.startswith("audio/"):
            return "Musicas"
        if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".zip", ".rar", ".7z") or mime.startswith("application/"):
            return "Documentos"
        if ext in (".webp", ".tgs"):
            return "Stickers"

    return "Outros"


def get_media_size(message):
    file_obj = getattr(message, "file", None)
    return (getattr(file_obj, "size", None) or 0) if file_obj else 0


def get_file_size(path):
    try:
        return Path(path).stat().st_size
    except (FileNotFoundError, OSError):
        return None


def existing_file_status(target, expected_size):
    """Classifica um arquivo já existente sem confundir arquivo completo
    com parcial. Mantido da V5.6 para o relatório e para a UI."""
    target = Path(target)
    if not target.exists():
        return "missing"
    actual = get_file_size(target)
    if actual is None:
        return "missing"
    if expected_size:
        return "complete" if actual == expected_size else "incomplete"
    return "unknown"


def verify_download(path, expected_size):
    path = Path(path)
    if not path.exists():
        return False
    actual_size = get_file_size(path)
    if actual_size is None:
        return False
    if expected_size:
        return actual_size == expected_size
    return actual_size > 0


def topic_folder_name(topic_number):
    try:
        return f"Modulo {int(topic_number):02d}"
    except (TypeError, ValueError):
        return f"Modulo {topic_number}"


def get_chat_identity(chat):
    title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(getattr(chat, "id", "desconhecido"))
    username = (getattr(chat, "username", None) or "").strip()
    chat_id = str(getattr(chat, "id", "desconhecido"))

    if username:
        username = username.lstrip("@")
        chat_link = f"https://t.me/{username}"
        channel_ref = f"@{username}"
    else:
        chat_link = ""
        channel_ref = f"ID {chat_id}"

    return {"title": str(title), "username": username, "id": chat_id, "link": chat_link, "reference": channel_ref}


def format_bytes(value):
    value = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def format_speed(value):
    return f"{format_bytes(value)}/s"


def format_eta(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def write_download_log(destination, state, stats, started_at):
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    # One fixed report represents the complete queue for the selected link.
    # A new queue refreshes this global report in the same destination folder.
    log_path = destination / LOG_FILENAME

    entries = list(state.get("log_entries", []))
    lines = [
        "TG DOWNLOADER - RELATORIO GLOBAL",
        f"Inicio: {started_at}",
        f"Fim:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Destino: {destination}",
        "",
        f"Total de arquivos: {stats.get('total', 0)}",
        "",
        "ARQUIVOS",
    ]

    if not entries:
        lines.append("Nenhum arquivo de mídia foi processado.")
    else:
        for entry in entries:
            status = {
                "BAIXADO": "ok",
                "PULADO - JA EXISTIA": "pulado",
                "ERRO": "erro",
                "INTERROMPIDO": "interrompido",
                "PENDENTE": "pendente",
                "REMOVIDO": "removido",
            }.get(entry.get("status", "PENDENTE"), str(entry.get("status", "pendente")).lower())
            filename = entry.get("filename", "")
            lines.append(f"{entry.get('index', '?')}-{filename} status: {status}")
            if status in {"erro", "interrompido", "pendente"} and entry.get("reason"):
                lines.append(f"  Detalhes: {entry['reason']}")

    lines += [
        "",
        f"Resumo: {stats.get('downloaded', 0)} ok | "
        f"{stats.get('skipped', 0)} pulados | {stats.get('errors', 0)} erros",
        "",
    ]

    try:
        atomic_write_text(log_path, "\n".join(lines))
        return log_path
    except Exception:
        return None


def load_manifest(destination):
    path = Path(destination) / MANIFEST_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("O manifesto de downloads esta ilegivel; preserve-o antes de tentar novamente.") from None
    if not isinstance(data, dict):
        raise RuntimeError("O manifesto de downloads deve conter um objeto JSON.")
    return data


def save_manifest(destination, manifest):
    path = Path(destination) / MANIFEST_FILENAME
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2))


def atomic_write_text(path, text):
    """Replace a small state file only after its complete contents reach disk."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def safe_manifest_target(destination, relpath):
    if not isinstance(relpath, str) or not relpath or Path(relpath).is_absolute():
        raise RuntimeError("O manifesto contem um caminho de arquivo invalido.")
    root = Path(destination).resolve()
    target = root / relpath
    if not target.resolve().is_relative_to(root) or target.resolve() == root or target.is_symlink():
        raise RuntimeError("O manifesto contem um caminho fora da pasta de destino.")
    return target


def resolve_unique_path(directory, stem, ext, reserved_paths):
    candidate = directory / f"{stem}{ext}"
    counter = 1
    while str(candidate) in reserved_paths or candidate.exists() or candidate.is_symlink():
        candidate = directory / f"{stem} ({counter}){ext}"
        counter += 1
    return candidate


def recompute_transfer_state(state):
    downloads = state.get("downloads", [])
    active_objects = {id(item["obj"]) for item in state.get("active", {}).values()}
    counts = {status: 0 for status in TERMINAL_STATUSES | {"queued", "paused", "downloading"}}
    pending_count = 0
    pending_preview = []
    completed_bytes = 0
    global_bytes = 0
    discovered_total = 0
    speed = 0.0
    remaining = 0
    for item in downloads:
        if item.status not in TERMINAL_STATUSES:
            if not item.resume_event.is_set():
                item.status = "paused"
                item.speed = 0
                item.eta = None
            elif item.status == "paused" and not state.get("stop"):
                item.status = "downloading" if id(item) in active_objects else "queued"
        counts[item.status] = counts.get(item.status, 0) + 1
        active = id(item) in active_objects
        if item.status in {"queued", "paused"} and not active:
            pending_count += 1
            if len(pending_preview) < 6:
                pending_preview.append(item.filename)
        if item.status in {"complete", "skipped"}:
            completed_bytes += item.current_bytes
        if item.status != "removed":
            global_bytes += item.current_bytes
            discovered_total += item.expected_size or item.current_bytes
        if item.status == "downloading" and active:
            speed += item.speed
        if item.status not in TERMINAL_STATUSES:
            remaining += max(0, item.expected_size - item.current_bytes)

    state["completed"] = counts["complete"]
    state["skipped"] = counts["skipped"]
    state["errors"] = counts["error"]
    state["removed"] = counts["removed"]
    state["status_counts"] = counts
    undiscovered = 0
    if state.get("catalog_complete"):
        undiscovered = max(0, state.get("catalog_total_files", 0) - len(downloads))
    state["pending_count"] = pending_count + undiscovered
    state["pending_preview"] = pending_preview
    state["_completed_bytes"] = completed_bytes
    state["global_bytes"] = global_bytes
    catalog_total = state.get("catalog_total_bytes", 0)
    state["global_total_bytes"] = (
        catalog_total
        if state.get("catalog_complete")
        else max(catalog_total, discovered_total)
    )
    state["speed"] = speed
    state["peak_speed"] = max(state.get("peak_speed", 0), speed)
    state["eta"] = remaining / speed if speed > 0 else None


def _make_log_entry(item):
    return {
        "index": item.index, "filename": item.filename, "status": "PENDENTE",
        "channel": item.chat_info["title"], "channel_ref": item.chat_info["reference"],
        "channel_id": item.chat_info["id"], "channel_link": item.chat_info["link"],
        "message_link": item.message_link, "path": str(item.target),
        "reason": "", "size": 0, "expected_size": item.expected_size,
    }


def _mark_removed(state, item):
    if item.status == "removed":
        return
    item.status = "removed"
    item.speed = 0
    item.eta = None
    item.log_entry.update(status="REMOVIDO", reason="Removido da fila. Parcial preservado.", size=item.current_bytes)
    add_recent(state, item.index, item.filename, "REMOVIDO", "-")
    add_log(state, "WARN", f"{item.filename}: removido da fila; parcial preservado.")


async def _wait_ready(item, state):
    if item.removed:
        raise DownloadRemoved()
    if state.get("stop"):
        raise DownloadStopped()
    while not item.resume_event.is_set():
        item.status = "paused"
        item.speed = 0
        item.eta = None
        await asyncio.sleep(0.1)
        if item.removed:
            raise DownloadRemoved()
        if state.get("stop"):
            raise DownloadStopped()
    item.status = "downloading"


async def _wait_retry(seconds, item, state):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if state.get("stop"):
            raise DownloadStopped()
        if item.removed:
            raise DownloadRemoved()
        await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))


def discard_partial_download(part_path, download_obj=None):
    """Remove a partial after an integrity failure or a non-resumable error."""
    part_path = Path(part_path)
    if part_path.is_symlink():
        raise RuntimeError("O arquivo parcial e um link simbolico.")
    part_path.unlink(missing_ok=True)
    if download_obj is not None:
        download_obj.current_bytes = 0
        download_obj.speed = 0
        download_obj.eta = None


async def download_file_chunked(client, download_obj, part_path, state):
    await _wait_ready(download_obj, state)
    part_path = Path(part_path)
    if part_path.is_symlink():
        raise RuntimeError("O arquivo parcial e um link simbolico.")
    current_size = get_file_size(part_path) or 0
    if download_obj.expected_size and current_size == download_obj.expected_size:
        download_obj.current_bytes = current_size
        return part_path
    offset = current_size - current_size % DOWNLOAD_CHUNK_SIZE
    if download_obj.expected_size and offset >= download_obj.expected_size:
        offset = 0
    stream = client.iter_download(
        download_obj.message, offset=offset, request_size=DOWNLOAD_CHUNK_SIZE,
    ).__aiter__()
    last_time = time.monotonic()
    last_bytes = offset
    download_obj.current_bytes = offset
    try:
        try:
            output_context = part_path.open(
                "r+b" if part_path.exists() else "wb", buffering=0,
            )
        except OSError as error:
            raise DownloadStorageError(str(error)) from error
        with output_context as output:
            try:
                output.seek(offset)
                output.truncate()
            except OSError as error:
                raise DownloadStorageError(str(error)) from error
            while True:
                was_paused = not download_obj.resume_event.is_set()
                await _wait_ready(download_obj, state)
                if was_paused:
                    last_time = time.monotonic()
                    last_bytes = download_obj.current_bytes
                try:
                    chunk = await asyncio.wait_for(
                        anext(stream), timeout=CHUNK_TIMEOUT_SECONDS,
                    )
                except StopAsyncIteration:
                    break
                await _wait_ready(download_obj, state)
                if not chunk:
                    break
                try:
                    output.write(chunk)
                except OSError as error:
                    raise DownloadStorageError(str(error)) from error
                download_obj.current_bytes += len(chunk)
                elapsed = time.monotonic() - last_time
                if elapsed >= 0.25:
                    instantaneous = (download_obj.current_bytes - last_bytes) / elapsed
                    download_obj.speed = instantaneous if download_obj.speed <= 0 else 0.65 * download_obj.speed + 0.35 * instantaneous
                    last_time = time.monotonic()
                    last_bytes = download_obj.current_bytes
                if download_obj.speed > 0 and download_obj.expected_size:
                    download_obj.eta = max(0, (download_obj.expected_size - download_obj.current_bytes) / download_obj.speed)
                if download_obj.expected_size and download_obj.current_bytes >= download_obj.expected_size:
                    break
                await asyncio.sleep(0)
    finally:
        close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result
    return part_path


async def refresh_download_message(client, chat, item):
    """Fetch a fresh media reference after Telegram expires the old one."""
    if hasattr(client, "refresh_message"):
        message = await client.refresh_message(chat, item.message_id)
    else:
        message = await client.get_messages(chat, ids=item.message_id)
    if message is None or not message_has_media(message):
        raise RuntimeError("A mensagem original nao esta mais disponivel no Telegram.")
    item.message = message
    return message


async def process_single_download(
    worker_id, client, item, state, destination, manifest, manifest_lock,
    reserved_paths=None, chat=None,
):
    part_path = Path(str(item.target) + ".part")
    state["active"][worker_id] = {"obj": item}
    add_log(state, "INFO", f"Iniciando {item.filename}")
    try:
        attempt = 0
        transient_attempt = 0
        reference_refreshes = 0
        while True:
            await _wait_ready(item, state)
            try:
                await download_file_chunked(client, item, part_path, state)
                await _wait_ready(item, state)
                if not verify_download(part_path, item.expected_size):
                    raise DownloadIntegrityError(
                        "O arquivo recebido nao passou na verificacao de tamanho."
                    )
                try:
                    os.replace(part_path, item.target)
                except OSError as error:
                    raise DownloadStorageError(str(error)) from error
                item.current_bytes = get_file_size(item.target) or 0
                # A single canonical chat key is used for both scan and completion.
                async with manifest_lock:
                    manifest[item.chat_info["id"]][str(item.message_id)].update(
                        expected_size=item.current_bytes, status="complete",
                    )
                    manifest[item.chat_info["id"]][str(item.message_id)].pop("last_error", None)
                    try:
                        save_manifest(destination, manifest)
                    except OSError as error:
                        # The verified target is already in place. A later scan
                        # repairs the manifest from its exact size, so never
                        # download the same file again only because this
                        # checkpoint failed.
                        add_log(
                            state, "WARN",
                            f"{item.filename}: arquivo concluido, mas o manifesto "
                            f"nao foi atualizado ({type(error).__name__}).",
                        )
                item.status = "complete"
                item.completed_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                item.log_entry.update(status="BAIXADO", size=item.current_bytes, reason="Download concluido e verificado.")
                state["categories"][item.category] += 1
                add_recent(state, item.index, item.filename, "BAIXADO")
                add_log(state, "OK", f"{item.filename} concluido")
                return
            except (DownloadRemoved, DownloadStopped, asyncio.CancelledError):
                raise
            except FileReferenceExpiredError:
                reference_refreshes += 1
                item.speed = 0
                item.eta = None
                if reference_refreshes > MAX_FILE_REFERENCE_REFRESHES:
                    raise RuntimeError(
                        "A referencia do arquivo continuou expirada apos ser renovada."
                    ) from None
                if chat is None:
                    raise RuntimeError(
                        "A referencia do arquivo expirou e o chat de origem nao esta disponivel."
                    ) from None
                add_log(
                    state, "WARN",
                    f"{item.filename}: referencia expirada; renovando no Telegram "
                    f"({reference_refreshes}/{MAX_FILE_REFERENCE_REFRESHES}).",
                )
                await refresh_download_message(client, chat, item)
                continue
            except FloodWaitError as error:
                item.speed = 0
                item.eta = None
                if item.removed:
                    raise DownloadRemoved()
                if state.get("stop"):
                    raise DownloadStopped()
                add_log(state, "WARN", f"Limite do Telegram: aguardando {error.seconds}s para {item.filename}.")
                await _wait_retry(error.seconds, item, state)
                continue
            except DownloadStorageError as error:
                raise RuntimeError(
                    f"Falha de armazenamento; verifique se o disco continua "
                    f"montado e gravavel. Parcial preservado ({error})."
                ) from error
            except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as error:
                if isinstance(error, DownloadIntegrityError):
                    attempt += 1
                    discard_partial_download(part_path, item)
                    if attempt >= MAX_DOWNLOAD_RETRIES:
                        raise
                    add_log(
                        state, "WARN",
                        f"{item.filename}: verificacao falhou; reiniciando do zero "
                        f"({attempt}/{MAX_DOWNLOAD_RETRIES}).",
                    )
                    await _wait_retry(RETRY_BACKOFF_SECONDS, item, state)
                    continue
                transient_attempt += 1
                item.speed = 0
                item.eta = None
                item.current_bytes = get_file_size(part_path) or 0
                if transient_attempt > MAX_TRANSIENT_RETRIES:
                    raise RuntimeError(
                        f"Conexao sem progresso apos {MAX_TRANSIENT_RETRIES} tentativas; "
                        "o parcial foi preservado."
                    ) from error
                delay = min(
                    MAX_RETRY_BACKOFF_SECONDS,
                    RETRY_BACKOFF_SECONDS * 2 ** min(transient_attempt - 1, 5),
                )
                add_log(
                    state, "WARN",
                    f"{item.filename}: conexao interrompida ({type(error).__name__}); "
                    f"retomando em {delay}s a partir de {format_bytes(item.current_bytes)} "
                    f"({transient_attempt}/{MAX_TRANSIENT_RETRIES}).",
                )
                await _wait_retry(delay, item, state)
                continue
            except Exception as error:
                if item.removed:
                    raise DownloadRemoved()
                if state.get("stop"):
                    raise DownloadStopped()
                attempt += 1
                discard_partial_download(part_path, item)
                if attempt >= MAX_DOWNLOAD_RETRIES:
                    raise
                item.speed = 0
                add_log(
                    state, "WARN",
                    f"{item.filename}: tentativa {attempt}/{MAX_DOWNLOAD_RETRIES} "
                    f"({type(error).__name__}); reiniciando o arquivo inteiro.",
                )
                await _wait_retry(RETRY_BACKOFF_SECONDS, item, state)
    except DownloadRemoved:
        _mark_removed(state, item)
    except (DownloadStopped, asyncio.CancelledError):
        if item.removed:
            _mark_removed(state, item)
        else:
            item.status = "paused"
            item.resume_event.clear()
            item.log_entry.update(status="INTERROMPIDO", reason="Parcial preservado para retomada.", size=item.current_bytes)
            raise
    except Exception as error:
        item.status = "error"
        item.error = f"{type(error).__name__}: {error}"
        item.log_entry.update(status="ERRO", reason=item.error, size=item.current_bytes)
        try:
            async with manifest_lock:
                chat_manifest = manifest.get(item.chat_info["id"], {})
                entry = chat_manifest.get(str(item.message_id)) if isinstance(chat_manifest, dict) else None
                if isinstance(entry, dict):
                    entry.update(status="error", last_error=item.error)
                    save_manifest(destination, manifest)
        except Exception as manifest_error:
            add_log(
                state, "WARN",
                f"{item.filename}: nao foi possivel registrar o erro "
                f"({type(manifest_error).__name__}).",
            )
        add_recent(state, item.index, item.filename, "ERRO", "!")
        add_log(state, "ERR", f"{item.filename}: {item.error}")
    finally:
        item.speed = 0
        item.eta = None
        state["active"].pop(worker_id, None)
        if item.status in TERMINAL_STATUSES:
            # A full Telegram Message may carry captions, entities and previews.
            # Keep only the stable ID after completion to bound long-run memory.
            item.message = None
        recompute_transfer_state(state)


def _execution_stats(state):
    return {
        "total": state["total_files"], "downloaded": state["completed"],
        "skipped": state["skipped"], "errors": state["errors"], "removed": state["removed"],
        "incomplete": state["incomplete"], "bytes": state["_completed_bytes"],
    }


async def download_messages(client, chat, destination, state):
    destination = Path(destination).expanduser().resolve()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    workers = []
    queue = None
    with destination_lock(destination):
        fresh = create_state(state["slot"], state["concurrency"])
        for key in (
            "total_files", "completed", "skipped", "errors", "removed", "global_bytes",
            "global_total_bytes", "_completed_bytes", "speed", "peak_speed", "eta",
            "catalog_total_files", "catalog_total_bytes", "catalog_complete", "discovered_files",
            "active", "downloads", "pending_count", "pending_preview", "categories",
            "incomplete", "recent", "log_entries", "log_path", "status_counts",
        ):
            state[key] = fresh[key]
        chat_info = get_chat_identity(chat)
        state.update(
            chat_title=chat_info["title"], chat_username=chat_info["username"],
            destination=str(destination), phase="sizing", phase_label="CALCULANDO VOLUME TOTAL",
        )
        add_log(state, "INFO", "Calculando o volume total sem carregar mensagens na memoria.")
        try:
            catalog_files = 0
            catalog_bytes = 0
            async for message in client.iter_messages(chat, reverse=True):
                if state.get("stop"):
                    raise DownloadStopped()
                if not message_has_media(message):
                    continue
                catalog_files += 1
                catalog_bytes += get_media_size(message)
                state.update(
                    total_files=catalog_files,
                    catalog_total_files=catalog_files,
                    catalog_total_bytes=catalog_bytes,
                    global_total_bytes=catalog_bytes,
                )
                if catalog_files % 25 == 0:
                    await asyncio.sleep(0)
            state.update(
                total_files=catalog_files,
                catalog_total_files=catalog_files,
                catalog_total_bytes=catalog_bytes,
                global_total_bytes=catalog_bytes,
                catalog_complete=True,
                phase="scanning",
                phase_label="ESCANEANDO E BAIXANDO",
            )
            add_log(
                state, "OK",
                f"Volume total: {format_bytes(catalog_bytes)} em {catalog_files} arquivo(s).",
            )
            manifest = load_manifest(destination)
            chat_key = chat_info["id"]
            chat_manifest = manifest.setdefault(chat_key, {})
            if not isinstance(chat_manifest, dict):
                raise RuntimeError("Entradas invalidas no manifesto deste chat.")
            reserved_paths = set()
            for entries in manifest.values():
                if not isinstance(entries, dict):
                    continue
                for entry in entries.values():
                    if isinstance(entry, dict) and entry.get("relpath"):
                        with contextlib.suppress(RuntimeError):
                            reserved_paths.add(str(safe_manifest_target(destination, entry["relpath"])))
            manifest_lock = asyncio.Lock()
            queue = asyncio.Queue(maxsize=max(2, state["concurrency"] * 2))

            async def consume(worker_id):
                while True:
                    item = await queue.get()
                    try:
                        if item is None:
                            return
                        await process_single_download(
                            worker_id, client, item, state, destination, manifest, manifest_lock,
                            chat=chat,
                        )
                    finally:
                        queue.task_done()

            workers = [
                asyncio.create_task(consume(worker_id), name=f"tg-worker-{worker_id}")
                for worker_id in range(state["concurrency"])
            ]
            async for message in client.iter_messages(chat, reverse=True):
                if state.get("stop"):
                    raise DownloadStopped()
                if not message_has_media(message):
                    continue
                index = len(state["downloads"]) + 1
                category = get_category(message)
                expected_size = get_media_size(message)
                key = str(message.id)
                entry = chat_manifest.get(key)
                legacy = manifest.get(str(getattr(message, "chat_id", "")), {})
                legacy_entry = legacy.get(key) if isinstance(legacy, dict) else None
                if isinstance(legacy_entry, dict) and (
                    not entry or entry.get("status") != "complete" and legacy_entry.get("status") == "complete"
                ):
                    entry = dict(legacy_entry)
                    chat_manifest[key] = entry
                if entry is not None:
                    if not isinstance(entry, dict):
                        raise RuntimeError("Entrada de arquivo invalida no manifesto.")
                    target = safe_manifest_target(destination, entry.get("relpath"))
                else:
                    stem, ext, topic = build_filename_parts(message)
                    ext = sanitize_filename(ext) if ext else ""
                    directory = destination / category
                    if topic:
                        directory /= topic_folder_name(topic)
                    directory = safe_manifest_target(destination, str(directory.relative_to(destination)))
                    target = resolve_unique_path(directory, stem, ext, reserved_paths)
                    entry = {"relpath": str(target.relative_to(destination)), "expected_size": expected_size, "status": "pending"}
                    chat_manifest[key] = entry
                reserved_paths.add(str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                item = Download(index, message, target.name, target, category, expected_size, get_message_link(chat, message.id), chat_info)
                item.log_entry = _make_log_entry(item)
                state["downloads"].append(item)
                state["log_entries"].append(item.log_entry)
                state["discovered_files"] = len(state["downloads"])
                known_size = expected_size or entry.get("expected_size", 0)
                if (entry.get("status") == "complete" or expected_size) and verify_download(target, known_size):
                    item.status = "skipped"
                    item.current_bytes = get_file_size(target) or 0
                    item.completed_at = datetime.fromtimestamp(target.stat().st_mtime).strftime("%d/%m/%Y %H:%M:%S")
                    item.log_entry.update(status="PULADO - JA EXISTIA", size=item.current_bytes, reason="Arquivo completo encontrado.")
                    state["categories"][category] += 1
                    entry.update(status="complete", expected_size=item.current_bytes)
                    add_recent(state, index, item.filename, "JA EXISTE")
                    add_log(state, "INFO", f"{item.filename} ja existe (pulado)")
                    item.message = None
                else:
                    if existing_file_status(target, expected_size) == "incomplete":
                        state["incomplete"] += 1
                        item.log_entry["reason"] = "Arquivo existente incompleto; sera substituido depois da verificacao."
                        add_log(state, "WARN", f"{item.filename} estava incompleto.")
                    partial_size = get_file_size(Path(str(target) + ".part")) or 0
                    item.current_bytes = min(partial_size, expected_size) if expected_size else partial_size
                    # A fila limitada cria backpressure: o histórico não ocupa
                    # toda a RAM antes de o primeiro arquivo começar a baixar.
                    await queue.put(item)
                recompute_transfer_state(state)
                if index % 25 == 0:
                    await asyncio.sleep(0)
            async with manifest_lock:
                save_manifest(destination, manifest)
            state.update(phase="downloading", phase_label="FINALIZANDO DOWNLOADS")
            await queue.join()
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)
            workers.clear()
            state.update(phase="complete", phase_label="DOWNLOAD FINALIZADO")
            add_log(state, "OK", f"Finalizado: {state['completed']} baixados, {state['skipped']} existentes, {state['errors']} erros.")
        except DownloadStopped:
            state.update(phase="stopped", phase_label="INTERROMPIDO")
        except asyncio.CancelledError:
            state["stop"] = True
            state.update(phase="stopped", phase_label="INTERROMPIDO")
            raise
        except Exception:
            state.update(phase="error", phase_label="ERRO")
            raise
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            recompute_transfer_state(state)
            stats = _execution_stats(state)
            log_path = write_download_log(destination, state, stats, started_at)
            state["log_path"] = str(log_path) if log_path else ""
        return _execution_stats(state)
