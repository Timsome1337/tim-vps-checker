"""Strict and simple configuration validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import yaml

ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")


@dataclass(frozen=True)
class Server:
    id: str
    name: str
    enabled: bool
    mode: str
    host: str | None
    port: int
    username: str
    key_path: str
    provider: str
    tariff: str
    price: str
    expires_at: date | None
    services: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    servers: tuple[Server, ...]
    check_interval_seconds: int
    cpu_alert_percent: int
    memory_alert_percent: int
    disk_alert_percent: int
    expiry_reminder_days: tuple[int, ...]


def _date(value: object, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label}: use YYYY-MM-DD or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: use YYYY-MM-DD or null") from exc


def _limit(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name}: integer from {low} to {high} required")
    return value


def load_config(path: str | Path) -> Config:
    with Path(path).expanduser().open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError("Invalid YAML: configuration must be a mapping")
    raw_servers = raw.get("servers")
    if not isinstance(raw_servers, list) or not raw_servers:
        raise ValueError("Add at least one server to config.yaml")
    servers: list[Server] = []
    for item in raw_servers:
        if not isinstance(item, dict):
            raise ValueError("Each server must be a YAML mapping")
        sid = item.get("id", "")
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            raise ValueError("Server id: only a-z, 0-9, _, -; max 32 characters")
        if sid in {s.id for s in servers}:
            raise ValueError(f"Duplicate server id: {sid}")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"{sid}.enabled must be true/false")
        mode = item.get("mode", "local")
        if mode not in ("local", "ssh"):
            raise ValueError(f"{sid}.mode must be local or ssh")
        host = item.get("host") or None
        if host is not None and not isinstance(host, str):
            raise ValueError(f"{sid}.host must be a string or null")
        if enabled and mode == "ssh" and not host:
            raise ValueError(f"{sid}.host must be set for enabled SSH monitoring")
        key_path = item.get("key_path", "~/.ssh/vps-monitor")
        if not isinstance(key_path, str):
            raise ValueError(f"{sid}.key_path must be a path")
        raw_services = item.get("services", [])
        if not isinstance(raw_services, list) or any(not isinstance(s, str) or not SERVICE_RE.fullmatch(s) for s in raw_services):
            raise ValueError(f"{sid}.services must list valid systemd unit names without '.service'")
        servers.append(Server(
            id=sid,
            name=str(item.get("name") or sid)[:100],
            enabled=enabled,
            mode=mode,
            host=host,
            port=_limit(item.get("port", 22), f"{sid}.port", 1, 65535),
            username=str(item.get("username") or "vpsmonitor"),
            key_path=key_path,
            provider=str(item.get("provider") or "Не указан"),
            tariff=str(item.get("tariff") or "Не указан"),
            price=str(item.get("price") or "Не указана"),
            expires_at=_date(item.get("expires_at"), f"{sid}.expires_at"),
            services=tuple(raw_services),
        ))
    reminder = raw.get("expiry_reminder_days", [14, 7, 3, 1, 0])
    if not isinstance(reminder, list) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 365 for x in reminder):
        raise ValueError("expiry_reminder_days must be a list of nonnegative integers <= 365")
    return Config(
        servers=tuple(servers),
        check_interval_seconds=_limit(raw.get("check_interval_seconds", 300), "check_interval_seconds", 60, 86400),
        cpu_alert_percent=_limit(raw.get("cpu_alert_percent", 95), "cpu_alert_percent", 1, 100),
        memory_alert_percent=_limit(raw.get("memory_alert_percent", 90), "memory_alert_percent", 1, 100),
        disk_alert_percent=_limit(raw.get("disk_alert_percent", 90), "disk_alert_percent", 1, 100),
        expiry_reminder_days=tuple(sorted(set(reminder), reverse=True)),
    )
