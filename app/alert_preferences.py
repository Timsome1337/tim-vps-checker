"""Local per-account notification switches. Never stores tokens or secrets."""
from __future__ import annotations
from pathlib import Path
from contextlib import contextmanager
import sqlite3

CATEGORIES = ('resources', 'services', 'billing', 'connection')
TITLES = {
    'resources': '📊 Нагрузка и диск',
    'services': '🛡️ Системные службы',
    'billing': '📅 Аренда и оплата',
    'connection': '🔌 Сбор метрик и восстановление',
}


def category(message: str) -> str:
    text = message.casefold()
    if '💳' in text or 'аренд' in text or 'оплат' in text:
        return 'billing'
    if 'сервис ' in text or 'служб' in text or '🔴' in text:
        return 'services'
    if any(word in text for word in ('cpu', 'озу', 'диск', 'свободного места', 'запас свободного', '⚠️ на vps')):
        return 'resources'
    return 'connection'


class AlertPreferences:
    """Allowlisted user IDs are checked by Telegram handlers, not this storage layer."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS preferences (
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                PRIMARY KEY (user_id, category))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS alert_deliveries (
                user_id INTEGER NOT NULL, category TEXT NOT NULL,
                last_sent INTEGER NOT NULL,
                PRIMARY KEY(user_id, category))""")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def enabled(self, user_id: int, name: str) -> bool:
        if name not in CATEGORIES:
            raise ValueError('Unknown alert category')
        with self._connect() as conn:
            row = conn.execute('SELECT enabled FROM preferences WHERE user_id=? AND category=?',
                               (user_id, name)).fetchone()
        return bool(row[0]) if row else True

    def toggle(self, user_id: int, name: str) -> bool:
        if name not in CATEGORIES:
            raise ValueError('Unknown alert category')
        with self._connect() as conn:
            row = conn.execute('SELECT enabled FROM preferences WHERE user_id=? AND category=?',
                               (user_id, name)).fetchone()
            value = not (bool(row[0]) if row else True)
            conn.execute("""INSERT INTO preferences (user_id,category,enabled) VALUES (?,?,?)
                ON CONFLICT(user_id,category) DO UPDATE SET enabled=excluded.enabled""",
                (user_id, name, int(value)))
        return value

    def mark_sent(self, user_id: int, name: str, timestamp: int | None = None) -> None:
        """Record successful automatic sends only; not a simulated alert."""
        import time
        if name not in CATEGORIES:
            raise ValueError('Unknown alert category')
        ts = int(time.time() if timestamp is None else timestamp)
        with self._connect() as conn:
            conn.execute("""INSERT INTO alert_deliveries(user_id,category,last_sent)
                VALUES(?,?,?) ON CONFLICT(user_id,category)
                DO UPDATE SET last_sent=excluded.last_sent""", (user_id, name, ts))

    def last_sent(self, user_id: int, name: str) -> int | None:
        if name not in CATEGORIES:
            raise ValueError('Unknown alert category')
        with self._connect() as conn:
            row = conn.execute('SELECT last_sent FROM alert_deliveries WHERE user_id=? AND category=?',
                               (user_id, name)).fetchone()
        return int(row[0]) if row else None
