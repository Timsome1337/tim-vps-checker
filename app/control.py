"""Restricted Telegram management actions for one Aeza service."""
from __future__ import annotations
from pathlib import Path
import json
import os
import secrets
import time

REBOOT_COOLDOWN = 30 * 60
CONFIRM_TTL = 90


def owner_allowed(uid: int | None, owner_id: int) -> bool:
    return uid is not None and uid > 0 and uid == owner_id


def prepare(pending: dict, uid: int, service_id: str, now: float | None = None) -> str:
    token = secrets.token_urlsafe(12)
    pending[uid] = (token, time.monotonic() if now is None else now, str(service_id))
    return token


def consume(pending: dict, uid: int, service_id: str, token: str,
            now: float | None = None) -> bool:
    entry = pending.pop(uid, None)
    if not entry:
        return False
    saved_token, started, saved_id = entry
    t = time.monotonic() if now is None else now
    return (t >= started and t - started <= CONFIRM_TTL and
            saved_id == str(service_id) and secrets.compare_digest(saved_token, token))


def reserve_action(path: Path, service_id: str, now: int | None = None) -> bool:
    """Reserve a reboot attempt before any network call (atomic file, no auto-retry).

    Retained across bot restarts; no secrets in the file.
    """
    ts = int(time.time()) if now is None else int(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raw = {}
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError('Не удалось прочитать журнал операций; действие заблокировано')
    prev = int(raw.get('timestamp', 0))
    if raw.get('service_id') == str(service_id) and ts - prev < REBOOT_COOLDOWN:
        return False
    import tempfile
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.control-', text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'service_id': str(service_id), 'timestamp': ts}, f)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return True
