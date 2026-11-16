"""Tim VPS Checker: persistent incident detection without daily reports.

All decisions are local, deterministic, and side-effect free. Polling every five
minutes means three high readings are approximately 15 minutes of sustained load.
"""
from __future__ import annotations
from datetime import date
from .config import Config, Server
def choose_reminder(days_remaining: int, thresholds: tuple[int, ...]) -> int | None:
    """Find the closest reminder threshold without sending past thresholds in a burst."""
    candidates = [n for n in thresholds if days_remaining <= n]
    return min(candidates) if candidates else None



RESOURCE_STREAK = 3
SERVICE_STREAK = 2
RECOVERY_STREAK = 2
FREE_SPACE_BYTES = 1_000_000_000


def _number(data: dict, key: str):
    try:
        value = data[key]
        n = float(value if isinstance(value, (float, int)) else value['percent'])
        if not 0 <= n <= 100:
            return None
        return n
    except (TypeError, ValueError, KeyError):
        return None


def evaluate_smart(server: Server, result: dict, state: dict, config: Config,
                   today: date | None = None) -> tuple[list[str], dict]:
    """Check for confirmed persistent incidents; returns messages + next state.

    No direct network calls. During missing samples, counters reset so separated
    spikes cannot be treated as consecutive. Service baselines start silent.
    """
    new = dict(state)
    if not server.enabled:
        return [], new
    messages: list[str] = []
    smart = dict(new.get('smart_alerts') or {})
    resources = dict(smart.get('resources') or {})
    services = dict(smart.get('services') or {})
    free_state = dict(smart.get('free') or {})
    if result.get('ok') and isinstance(result.get('data'), dict):
        data = result['data']
        if new.get('available') is False:
            messages.append(f'✅ {server.name}: соединение и сбор метрик восстановлены')
        new['available'] = True
        new['fail_count'] = 0

        for label, key, threshold in (
            ('CPU', 'cpu_percent', config.cpu_alert_percent),
            ('ОЗУ', 'memory', config.memory_alert_percent),
            ('Диск', 'disk', config.disk_alert_percent),
        ):
            entry = dict(resources.get(key) or {})
            high = bool(entry.get('active'))
            value = _number(data, key)
            if value is None:
                entry['bad'] = entry['good'] = 0
            elif value >= threshold:
                entry['bad'] = int(entry.get('bad', 0)) + 1
                entry['good'] = 0
                if entry['bad'] >= RESOURCE_STREAK and not high:
                    messages.append(f'⚠️ {server.name}: {label} {value:.1f}% '
                                    f'(порог {threshold}%, {RESOURCE_STREAK} проверки подряд)')
                    entry['active'] = True
            elif value <= threshold - 5:
                entry['good'] = int(entry.get('good', 0)) + 1
                entry['bad'] = 0
                if high and entry['good'] >= RECOVERY_STREAK:
                    messages.append(f'✅ {server.name}: {label} восстановился до {value:.1f}%')
                    entry['active'] = False
            else:
                entry['bad'] = entry['good'] = 0
            resources[key] = entry

        current_services = data.get('services') or {}
        if isinstance(current_services, dict):
            legacy = new.get('services') or {}
            for name, status in current_services.items():
                if not isinstance(name, str) or not isinstance(status, str):
                    continue
                entry = dict(services.get(name) or {})
                if not entry:
                    prev = legacy.get(name)
                    entry = {'expected': prev == 'active' or status == 'active',
                             'active_incident': False, 'bad': 0, 'good': 0}
                if status == 'active':
                    entry['expected'] = True
                    entry['bad'] = 0
                    entry['good'] = int(entry.get('good', 0)) + 1
                    if entry.get('active_incident') and entry['good'] >= RECOVERY_STREAK:
                        messages.append(f'✅ {server.name}: сервис {name} снова active')
                        entry['active_incident'] = False
                elif status in ('inactive', 'failed') and entry.get('expected'):
                    entry['bad'] = int(entry.get('bad', 0)) + 1
                    entry['good'] = 0
                    if entry['bad'] >= SERVICE_STREAK and not entry.get('active_incident'):
                        messages.append(f'🔴 {server.name}: сервис {name} — {status} '
                                        f'({SERVICE_STREAK} проверки подряд)')
                        entry['active_incident'] = True
                else:
                    entry['bad'] = entry['good'] = 0
                services[name] = entry
            new['services'] = dict(current_services)

        disk = data.get('disk') or {}
        try:
            total, used = int(disk['total']), int(disk['used'])
            free = total - used
            valid = total > 0 and 0 <= used <= total
        except (ValueError, TypeError, KeyError):
            valid = False
        if valid:
            low = free < FREE_SPACE_BYTES
            if low:
                free_state['bad'] = int(free_state.get('bad', 0)) + 1
                free_state['good'] = 0
                if free_state['bad'] >= SERVICE_STREAK and not free_state.get('active'):
                    messages.append(f'⚠️ На VPS менее 1 ГБ свободного места: {free / 1e9:.2f} ГБ.')
                    free_state['active'] = True
            elif free > FREE_SPACE_BYTES + 300_000_000:
                free_state['good'] = int(free_state.get('good', 0)) + 1
                free_state['bad'] = 0
                if free_state.get('active') and free_state['good'] >= RECOVERY_STREAK:
                    messages.append(f'✅ На VPS восстановлен запас свободного места: {free / 1e9:.2f} ГБ.')
                    free_state['active'] = False
            else:
                free_state['bad'] = free_state['good'] = 0
        else:
            free_state['bad'] = free_state['good'] = 0
    else:
        failures = int(new.get('fail_count', 0)) + 1
        new['fail_count'] = failures
        if failures >= 2 and new.get('available') is not False:
            messages.append(f'🚨 {server.name}: проверка не удалась два раза подряд. Проверьте сбор метрик.')
            new['available'] = False
        for entry in resources.values():
            entry['bad'] = entry['good'] = 0
        for entry in services.values():
            entry['bad'] = entry['good'] = 0
        free_state['bad'] = free_state['good'] = 0

    smart['resources'] = resources
    smart['services'] = services
    smart['free'] = free_state
    new['smart_alerts'] = smart
    if server.expires_at:
        days = (server.expires_at - (today or date.today())).days
        band = choose_reminder(days, config.expiry_reminder_days)
        tag = f'{server.expires_at.isoformat()}:{band}'
        if band is not None and new.get('reminder_sent') != tag:
            if days < 0:
                msg = f'💳 {server.name}: дата аренды прошла {-days} дн. назад'
            elif days == 0:
                msg = f'💳 {server.name}: сегодня заканчивается аренда'
            else:
                msg = f'💳 {server.name}: до окончания аренды {days} дн.'
            messages.append(msg + ' (проверьте информацию в Aeza)')
            new['reminder_sent'] = tag
    return messages, new
