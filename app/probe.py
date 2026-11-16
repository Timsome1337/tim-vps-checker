"""Collect read-only Linux metrics either locally or over trusted SSH."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from .config import Server

# This code executes on the target Linux VPS using its standard Python 3.
# No packages are installed on the monitored VPS.
PROBE_SCRIPT = r'''
import json
import os
import platform
import re
import shutil
import subprocess
import time

SERVICES = __SERVICES_LITERAL__

def read_cpu():
    line = open('/proc/stat', encoding='utf8').readline().split()
    n = [int(s) for s in line[1:]]
    idle = n[3] + (n[4] if len(n) > 4 else 0)
    return sum(n), idle

def cpu_percent():
    a, ai = read_cpu()
    time.sleep(0.4)
    b, bi = read_cpu()
    return round(100.0 * (1.0 - (bi-ai)/max(b-a, 1)), 1)

def mem():
    result = {}
    with open('/proc/meminfo', encoding='utf8') as f:
        for line in f:
            key, _, val = line.partition(':')
            try:
                result[key] = int(val.strip().split()[0]) * 1024
            except (IndexError, ValueError):
                pass
    total = result['MemTotal']
    used = max(0, total - result.get('MemAvailable', total))
    return {'total': total, 'used': used, 'percent': round(used * 100 / total, 1)}

def os_name():
    try:
        with open('/etc/os-release', encoding='utf8') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()

def cpu_model():
    try:
        with open('/proc/cpuinfo', encoding='utf8') as f:
            for line in f:
                if line.startswith(('model name', 'Hardware', 'Processor')):
                    return line.split(':', 1)[-1].strip()[:120]
    except OSError:
        pass
    return platform.processor() or 'unknown'

def net_totals():
    """Read only the host's default-route interface, never sum virtual links.

    Counter values are lifetime interface totals, NOT an Aeza monthly quota.
    If route discovery fails, report unavailable rather than inventing 1 TB.
    """
    try:
        with open('/proc/net/route', encoding='ascii') as routes:
            candidates = []
            for line in list(routes)[1:]:
                bits = line.split()
                if len(bits) < 8 or bits[1] != '00000000':
                    continue
                iface = bits[0]
                flags = int(bits[3], 16)
                metric = int(bits[6])
                if not flags & 1 or iface == 'lo':
                    continue
                if iface.startswith(('veth', 'br-', 'wg', 'tun', 'tap', 'awgv')):
                    continue
                candidates.append((metric, iface))
        if not candidates:
            return {'rx': None, 'tx': None, 'interface': None}
        _, iface = min(candidates)
        base = '/sys/class/net/' + iface + '/statistics/'
        with open(base + 'rx_bytes', encoding='ascii') as f:
            rx = int(f.read().strip())
        with open(base + 'tx_bytes', encoding='ascii') as f:
            tx = int(f.read().strip())
        return {'rx': rx, 'tx': tx, 'interface': iface}
    except (OSError, ValueError, IndexError):
        return {'rx': None, 'tx': None, 'interface': None}


def network_live():
    """Short read-only sample of the default-route interface; Mbps = decimal bits/s."""
    start = time.monotonic()
    first = net_totals()
    time.sleep(0.8)
    second = net_totals()
    elapsed = time.monotonic() - start
    network = dict(second)
    network['rx_mbps'] = network['tx_mbps'] = None
    if not first.get('interface') or first.get('interface') != second.get('interface'):
        return network
    for name in ('rx', 'tx'):
        previous, current = first.get(name), second.get(name)
        if (isinstance(previous, int) and isinstance(current, int)
                and current >= previous and elapsed > 0):
            network[name + '_mbps'] = round((current - previous) * 8 / (elapsed * 1_000_000), 2)
    return network


def services():
    result = {}
    for name in SERVICES:
        try:
            p = subprocess.run(['systemctl', 'is-active', name],
                               capture_output=True, text=True, timeout=3, check=False)
            result[name] = (p.stdout.strip() or 'unknown')[:32]
        except Exception:
            result[name] = 'unknown'
    return result

usage = shutil.disk_usage('/')
with open('/proc/uptime', encoding='utf8') as f:
    uptime = int(float(f.readline().split()[0]))
info = {
    'hostname': platform.node()[:120],
    'os': os_name(),
    'kernel': platform.release(),
    'architecture': platform.machine(),
    'vcpus': os.cpu_count() or 0,
    'cpu_model': cpu_model(),
    'cpu_percent': cpu_percent(),
    'load_avg': [round(x, 2) for x in os.getloadavg()],
    'memory': mem(),
    'disk': {'total': usage.total, 'used': usage.used,
             'percent': round(usage.used * 100 / usage.total, 1)},
    'network': network_live(),
    'uptime_seconds': uptime,
    'services': services(),
}
print(json.dumps(info, ensure_ascii=False))
'''


def probe(server: Server) -> dict:
    """Returns {ok: bool, data?: dict, error?: safe display message}."""
    if not server.enabled:
        return {"ok": False, "error": "Мониторинг отключён"}
    script = PROBE_SCRIPT.replace("__SERVICES_LITERAL__", repr(list(server.services)))
    if server.mode == "local":
        try:
            p = subprocess.run([sys.executable, "-c", script], text=True,
                               capture_output=True, timeout=25, check=False)
            if p.returncode != 0:
                return {"ok": False, "error": "Сбор метрик завершился ошибкой"}
            data = json.loads(p.stdout)
            return {"ok": True, "data": data}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return {"ok": False, "error": "Нет данных о локальном сервере"}

    # Import only when remote monitoring is required.
    try:
        import paramiko
        with paramiko.SSHClient() as client:
            client.load_system_host_keys()
            # Never trust previously unseen SSH host keys automatically.
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(hostname=server.host, port=server.port,
                           username=server.username,
                           key_filename=str(Path(server.key_path).expanduser()),
                           look_for_keys=False, allow_agent=False,
                           timeout=8, auth_timeout=8, banner_timeout=8)
            stdin, stdout, stderr = client.exec_command("python3 -", timeout=18)
            stdin.write(script)
            stdin.flush()
            stdin.channel.shutdown_write()
            output = stdout.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            if code:
                return {"ok": False, "error": "SSH есть, но сбор метрик не выполнен"}
            return {"ok": True, "data": json.loads(output)}
    except ImportError:
        return {"ok": False, "error": "Установите зависимости requirements.txt"}
    except Exception:
        # Do not echo sensitive server paths, usernames, IPs or SSH details in Telegram.
        return {"ok": False, "error": "Нет доступа по SSH или произошла ошибка проверки"}
