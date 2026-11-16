"""Read-only systemd self-test. Does NOT restart anything."""
from __future__ import annotations
import subprocess


def recovery_status(service: str = 'vps-monitor') -> tuple[bool | None, str]:
    """True = configured to start at boot and restart on crash; None = unavailable."""
    try:
        enabled = subprocess.run(['systemctl', 'is-enabled', service], text=True,
                                 capture_output=True, timeout=5, check=False)
        restart = subprocess.run(['systemctl', 'show', service, '-p', 'Restart', '--value'],
                                 text=True, capture_output=True, timeout=5, check=False)
        if enabled.returncode not in (0, 1) or restart.returncode:
            return None, 'Не удалось проверить systemd'
        is_enabled = enabled.stdout.strip() == 'enabled'
        policy = restart.stdout.strip()
        ok = is_enabled and policy in ('always', 'on-failure', 'on-abnormal', 'on-watchdog')
        return ok, ('Автозапуск включён; Restart=' + policy if ok else
                    'Проверьте автозапуск и Restart=' + (policy or 'unknown'))
    except (OSError, subprocess.TimeoutExpired):
        return None, 'Проверка systemd недоступна'
