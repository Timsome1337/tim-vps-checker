"""SQLite history for Tim VPS Checker.

One small local database, written by the existing five-minute background job.
No credentials, SSH paths, provider keys or chat IDs are stored.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

RETENTION_DAYS = 90
PERIODS = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400}


class HistoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS samples (
                    server_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    ok INTEGER NOT NULL,
                    cpu REAL, memory REAL, disk REAL,
                    memory_used INTEGER, memory_total INTEGER,
                    disk_used INTEGER, disk_total INTEGER,
                    uptime_seconds INTEGER,
                    PRIMARY KEY (server_id, ts)
                );
                CREATE INDEX IF NOT EXISTS samples_time_idx ON samples (ts);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_recent_idx ON events (server_id, ts DESC);
                CREATE TABLE IF NOT EXISTS network_samples (
                    server_id TEXT NOT NULL, ts INTEGER NOT NULL,
                    interface TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL,
                    PRIMARY KEY(server_id, ts)
                );
                CREATE INDEX IF NOT EXISTS network_time_idx ON network_samples(server_id, ts);
                CREATE TABLE IF NOT EXISTS service_states (
                    server_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (server_id, service)
                );
                CREATE TABLE IF NOT EXISTS control_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    finished_ts INTEGER,
                    actor_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS control_actions_recent_idx
                    ON control_actions (server_id, ts DESC);
            ''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(str(self.path), timeout=10)
        try:
            con.execute('PRAGMA busy_timeout=5000')
            with con:
                yield con
        finally:
            con.close()

    def record(self, server_id: str, result: dict, notifications: list[str] | tuple[str, ...] = (),
               check_interval_seconds: int = 300, timestamp: int | None = None) -> None:
        ts = int(time.time() if timestamp is None else timestamp)
        good = bool(result.get('ok') and isinstance(result.get('data'), dict))
        m = result.get('data', {}) if good else {}
        if good:
            try:
                cpu = float(m['cpu_percent'])
                memory = float(m['memory']['percent'])
                disk = float(m['disk']['percent'])
                mem_used = int(m['memory']['used'])
                mem_total = int(m['memory']['total'])
                disk_used = int(m['disk']['used'])
                disk_total = int(m['disk']['total'])
                uptime = int(m['uptime_seconds'])
            except (KeyError, ValueError, TypeError):
                good = False
        if not good:
            cpu = memory = disk = mem_used = mem_total = disk_used = disk_total = uptime = None
        with self.connect() as con:
            previous = con.execute(
                'SELECT ts, ok, uptime_seconds FROM samples WHERE server_id=? ORDER BY ts DESC LIMIT 1',
                (server_id,)).fetchone()
            con.execute('''INSERT OR REPLACE INTO samples
                (server_id, ts, ok, cpu, memory, disk, memory_used, memory_total,
                 disk_used, disk_total, uptime_seconds)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (server_id, ts, int(good), cpu, memory, disk, mem_used, mem_total,
                 disk_used, disk_total, uptime))
            if good:
                network = m.get('network') or {}
                if isinstance(network, dict):
                    iface, rx, tx = (network.get('interface'), network.get('rx'), network.get('tx'))
                    if (isinstance(iface, str) and iface and len(iface) <= 64
                            and type(rx) is int and type(tx) is int and rx >= 0 and tx >= 0):
                        con.execute("""INSERT OR REPLACE INTO network_samples
                            (server_id,ts,interface,rx,tx) VALUES (?,?,?,?,?)""",
                            (server_id, ts, iface, rx, tx))
            if previous and ts > previous[0]:
                elapsed = ts - previous[0]
                if elapsed > max(3 * check_interval_seconds, 900):
                    minutes = round(elapsed / 60)
                    con.execute('INSERT INTO events (server_id,ts,kind,description) VALUES (?,?,?,?)',
                                (server_id, ts, 'gap',
                                 f'Пауза в сборе показателей: около {minutes} мин. Причина неизвестна.'))
                if good and previous[1] and previous[2] is not None and uptime + 60 < previous[2]:
                    con.execute('INSERT INTO events (server_id,ts,kind,description) VALUES (?,?,?,?)',
                                (server_id, ts, 'reboot', 'Зафиксирована перезагрузка операционной системы.'))
            if good:
                current_services = m.get('services', {})
                if isinstance(current_services, dict):
                    for name, status in current_services.items():
                        if not isinstance(name, str) or not isinstance(status, str):
                            continue
                        prior = con.execute(
                            'SELECT status FROM service_states WHERE server_id=? AND service=?',
                            (server_id, name)).fetchone()
                        if prior and prior[0] != status:
                            con.execute(
                                'INSERT INTO events (server_id,ts,kind,description) VALUES (?,?,?,?)',
                                (server_id, ts, 'service',
                                 f'Состояние службы {name}: {prior[0]} → {status}'))
                        con.execute("INSERT INTO service_states (server_id,service,status) "
                                    "VALUES (?,?,?) ON CONFLICT(server_id,service) "
                                    "DO UPDATE SET status=excluded.status",
                                    (server_id, name, status))
            for msg in notifications:
                # Monitor alerts are already pre-formatted for Telegram; here they stay as plain text.
                con.execute('INSERT INTO events (server_id,ts,kind,description) VALUES (?,?,?,?)',
                            (server_id, ts, 'alert', str(msg)[:512]))
            cutoff = ts - RETENTION_DAYS * 86400
            con.execute('DELETE FROM samples WHERE ts<?', (cutoff,))
            con.execute('DELETE FROM network_samples WHERE ts<?', (cutoff,))
            con.execute('DELETE FROM events WHERE ts<?', (cutoff,))
            con.execute('DELETE FROM control_actions WHERE ts<?', (cutoff,))

    def add_event(self, server_id: str, kind: str, description: str,
                  timestamp: int | None = None) -> None:
        if kind not in ('startup', 'alert', 'service', 'reboot', 'gap'):
            raise ValueError('Invalid event kind')
        ts = int(time.time() if timestamp is None else timestamp)
        with self.connect() as con:
            con.execute('INSERT INTO events(server_id,ts,kind,description) VALUES (?,?,?,?)',
                        (server_id, ts, kind, description[:512]))

    def start_control_action(self, server_id: str, actor_id: int, action: str,
                             timestamp: int | None = None) -> int:
        """Create a durable audit record before a remote administrative request."""
        if action not in ('reboot', 'renewal_page') or actor_id <= 0:
            raise ValueError('Invalid control action')
        ts = int(time.time() if timestamp is None else timestamp)
        with self.connect() as con:
            cur = con.execute("""INSERT INTO control_actions
                (server_id,ts,actor_id,action,status,description)
                VALUES (?,?,?,?,?,?)""",
                (server_id, ts, actor_id, action, 'requested', ''))
            return int(cur.lastrowid)

    def finish_control_action(self, action_id: int, status: str,
                              description: str = '', timestamp: int | None = None) -> None:
        if status not in ('accepted', 'uncertain', 'page_shown', 'failed'):
            raise ValueError('Invalid action status')
        ts = int(time.time() if timestamp is None else timestamp)
        with self.connect() as con:
            cur = con.execute("""UPDATE control_actions SET finished_ts=?, status=?, description=?
                WHERE id=?""", (ts, status, description[:250], action_id))
            if cur.rowcount != 1:
                raise RuntimeError('Audit action not found')

    def recent_control_actions(self, server_id: str, limit: int = 10) -> list[dict]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""SELECT ts,actor_id,action,status,description
                FROM control_actions WHERE server_id=? ORDER BY ts DESC,id DESC LIMIT ?""",
                (server_id, max(1, min(int(limit), 30)))).fetchall()
        return [dict(row) for row in rows]

    def traffic_today(self, server_id: str, timestamp: int | None = None,
                      max_gap: int = 900) -> dict:
        """Measured increments from Samara midnight; NOT provider/accounting traffic.

        Unknown/reset counters or missing samples are not counted. This is a
        partial observed-day total, not a complete statement of billed traffic.
        """
        from zoneinfo import ZoneInfo
        now = int(time.time() if timestamp is None else timestamp)
        now_local = datetime.fromtimestamp(now, tz=ZoneInfo('Europe/Samara'))
        midnight = int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        with self.connect() as con:
            rows = con.execute("""SELECT ts,interface,rx,tx FROM network_samples
                WHERE server_id=? AND ts>=? AND ts<=? ORDER BY ts""",
                (server_id, midnight, now)).fetchall()
        result = {'rx': 0, 'tx': 0, 'samples': len(rows), 'intervals': 0,
                  'gaps': 0, 'first': rows[0][0] if rows else None,
                  'last': rows[-1][0] if rows else None}
        for prev, curr in zip(rows, rows[1:]):
            dt = curr[0] - prev[0]
            if (dt <= 0 or dt > max_gap or prev[1] != curr[1]
                    or curr[2] < prev[2] or curr[3] < prev[3]):
                result['gaps'] += 1
                continue
            result['rx'] += curr[2] - prev[2]
            result['tx'] += curr[3] - prev[3]
            result['intervals'] += 1
        return result

    def samples(self, server_id: str, period: str, timestamp: int | None = None) -> list[dict]:
        if period not in PERIODS:
            raise ValueError('Unknown period')
        now = int(time.time() if timestamp is None else timestamp)
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute('''SELECT ts, ok, cpu, memory, disk, disk_used, disk_total FROM samples
                WHERE server_id=? AND ts>=? AND ts<=? ORDER BY ts ASC''',
                (server_id, now - PERIODS[period], now)).fetchall()
        return [dict(row) for row in rows]

    def recent_events(self, server_id: str, limit: int = 12) -> list[dict]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute('SELECT ts, kind, description FROM events WHERE server_id=? ORDER BY ts DESC, id DESC LIMIT ?',
                               (server_id, max(1, min(limit, 30)))).fetchall()
        return [dict(row) for row in rows]


def fmt_time(ts: int) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ZoneInfo('Europe/Samara')).strftime('%d.%m %H:%M')


def summary(rows: list[dict]) -> dict:
    valid = [r for r in rows if r['ok'] and all(r[k] is not None for k in ('cpu', 'memory', 'disk'))]
    if not valid:
        return {'count': 0}
    out = {'count': len(valid), 'last_ts': valid[-1]['ts'],
           'total': len(rows), 'failed': len(rows) - len(valid),
           **{f'{metric}_{kind}': fun([float(r[metric]) for r in valid])
              for metric in ('cpu', 'memory', 'disk')
              for kind, fun in (('avg', lambda values: sum(values)/len(values)),
                                ('min', min), ('max', max))}}
    disk_rows = [r for r in valid if r.get('disk_used') is not None]
    if len(disk_rows) >= 2:
        out['disk_used_delta_bytes'] = disk_rows[-1]['disk_used'] - disk_rows[0]['disk_used']
    else:
        out['disk_used_delta_bytes'] = None
    return out


def russian_measurements(n: int) -> str:
    if n % 100 in (11, 12, 13, 14):
        suffix = 'измерений'
    elif n % 10 == 1:
        suffix = 'измерение'
    elif n % 10 in (2, 3, 4):
        suffix = 'измерения'
    else:
        suffix = 'измерений'
    return f'{n} {suffix}'
