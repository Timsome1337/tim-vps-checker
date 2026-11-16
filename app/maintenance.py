"""Local read-only SQLite integrity checks and transactional snapshots.

No external servers or automatic deletion of backups.
"""
from __future__ import annotations
from pathlib import Path
import os
import sqlite3
from contextlib import closing
from urllib.parse import quote


def _readonly_uri(path: Path) -> str:
    # A read-only SQLite URI prevents accidentally creating an empty DB.
    return 'file:' + quote(str(path.absolute()), safe='/') + '?mode=ro'


def verify_sqlite(path: str | Path) -> tuple[bool, str]:
    """Return a short diagnostic without logging database contents or user IDs."""
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        return False, 'Файл базы отсутствует или недоступен'
    try:
        with closing(sqlite3.connect(_readonly_uri(p), uri=True, timeout=5)) as con:
            row = con.execute('PRAGMA quick_check').fetchone()
        if row and row[0] == 'ok':
            return True, 'SQLite: целостность подтверждена'
        return False, 'SQLite: ошибка целостности'
    except sqlite3.Error:
        return False, 'SQLite: ошибка чтения'


def sqlite_snapshot(source: str | Path, destination: str | Path) -> None:
    """Write consistent, private snapshot using SQLite's online Backup API."""
    src, dst = Path(source), Path(destination)
    ok, reason = verify_sqlite(src)
    if not ok:
        raise RuntimeError(reason)
    if dst.exists() or dst.is_symlink():
        raise FileExistsError('Резервный файл уже существует')
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Pre-create the destination private *before* SQLite writes any data.
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(_readonly_uri(src), uri=True, timeout=10)) as reader:
            with closing(sqlite3.connect(dst, timeout=10)) as writer:
                reader.backup(writer)
        os.chmod(dst, 0o600)
        ok, reason = verify_sqlite(dst)
        if not ok:
            raise RuntimeError('Резервная копия не прошла проверку: ' + reason)
    except BaseException:
        if dst.exists():
            dst.unlink()
        raise
