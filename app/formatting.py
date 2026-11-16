"""Tim VPS Checker: mobile dashboard and Samara time (Telegram HTML).

All external values are HTML-escaped; Telegram messages do not need Premium emoji.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from html import escape
import math
from .config import Server

SEPARATOR = "────────────"
DISPLAY_TZ = ZoneInfo("Europe/Samara")


def samara_today() -> date:
    return datetime.now(DISPLAY_TZ).date()


def samara_time(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%H:%M") + " (Самара)"


def safe(value: object) -> str:
    return escape(str(value), quote=False)


def size(value: float) -> str:
    """Friendly decimal units: 1 МБ = 1,000,000 bytes, 1 ГБ = 1,000,000,000."""
    n = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"):
        if abs(n) < 1000 or unit == "ПБ":
            if unit == "Б":
                return f"{int(n)} Б"
            return f"{n:.1f}".replace(".", ",") + f" {unit}"
        n /= 1000
    raise AssertionError("unreachable")

def percent(value: float) -> str:
    n = float(value)
    return f"{n:.1f}".replace(".", ",") + "%"


def meter(value: float) -> str:
    """Compact eight-step indicator for detailed Telegram screens."""
    v = max(0.0, min(100.0, float(value)))
    cells = 8
    filled = min(cells, max(1, math.floor(v * cells / 100 + 0.5))) if v > 0 else 0
    color = "🔴" if v >= 90 else "🟡" if v >= 75 else "🟢"
    return f"{color} {'▰' * filled}{'▱' * (cells - filled)}  <b>{percent(v)}</b>"


def expiration(server: Server, today: date | None = None) -> str:
    """Keep this compatible with earlier tests/standalone check.py output."""
    today = today or samara_today()
    if server.expires_at is None:
        return "📅 Дата окончания: не указана"
    remaining = (server.expires_at - today).days
    if remaining < 0:
        suffix = f"просрочено на {-remaining} дн."
    elif remaining == 0:
        suffix = "последний день"
    else:
        suffix = f"осталось {remaining} дн."
    return f"📅 Дата окончания: {server.expires_at:%d.%m.%Y}\n⏳ До окончания: {suffix}"


def remaining_text(server: Server, today: date | None = None) -> str:
    if server.expires_at is None:
        return "Неизвестно"
    days = (server.expires_at - (today or samara_today())).days
    if days > 0:
        return f"{days} дн."
    if days == 0:
        return "Сегодня"
    return f"Просрочено на {-days} дн."


def uptime(seconds: int) -> str:
    d, rem = divmod(max(0, int(seconds)), 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d} д. {h} ч. {m} мин."


def short_status(server: Server, result: dict) -> str:
    """Used for multi-server fallback; separate lines per metric."""
    if not server.enabled:
        return f"⏸ <b>{safe(server.name)}</b>\nМониторинг отключён"
    if not result.get("ok"):
        return f"⚠️ <b>{safe(server.name)}</b>\nНет данных о нагрузке"
    m = result["data"]
    return (f"🟢 <b>{safe(server.name)}</b>\n"
            f"⚙️ Процессор: <b>{percent(m['cpu_percent'])}</b>\n"
            f"🧠 Память: <b>{percent(m['memory']['percent'])}</b>\n"
            f"💾 Диск: <b>{percent(m['disk']['percent'])}</b>")


def dashboard_text(server: Server, result: dict, *, checked_at: datetime | None = None) -> str:
    """Concise overview: separate metric rows, detailed stats only behind buttons."""
    lines = ["🛰 <b>TIM VPS CHECKER</b>",
             f"{safe(server.name)} · {safe(server.provider)}", ""]
    if not server.enabled:
        lines += ["⏸ <b>Мониторинг выключен</b>"]
    elif not result.get("ok"):
        lines += ["⚠️ <b>Показатели недоступны</b>",
                  "<i>Это не обязательно означает остановку VPS.</i>"]
    else:
        m = result["data"]
        lines += ["🟢 <b>Сервер доступен</b>", "",
                  "<b>📊 Ресурсы</b>",
                  f"⚙️ Процессор: <b>{percent(m['cpu_percent'])}</b>",
                  f"🧠 Оперативная память: <b>{percent(m['memory']['percent'])}</b>",
                  f"💾 Диск: <b>{percent(m['disk']['percent'])}</b>"]
    lines += ["", "<b>📅 Аренда</b>",
              f"⏳ Осталось: <b>{safe(remaining_text(server))}</b>",
              f"📆 Оплачено до: <b>{server.expires_at:%d.%m.%Y}</b>" if server.expires_at else
              "📆 Оплачено до: <b>неизвестно</b>",
              f"💶 Стоимость: <b>{safe(server.price)}</b>", "",
              f"<i>🕒 Проверено: {samara_time(checked_at)}</i>"]
    return "\n".join(lines)


def full_status(server: Server, result: dict) -> str:
    title = f"📊 <b>НАГРУЗКА VPS</b>\n{safe(server.name)}\n{SEPARATOR}\n"
    if not server.enabled:
        return title + "\n⏸ Мониторинг выключен\n\n" + safe(expiration(server))
    if not result.get("ok"):
        return (title + "\n⚠️ Показатели недоступны\n"
                + safe(result.get("error", "Ошибка сбора данных"))
                + "\n\n<i>Это не обязательно означает остановку VPS.</i>")
    m = result["data"]
    load_avg = " / ".join(str(x) for x in m["load_avg"])
    return (title + "\n🟢 <b>Сервер доступен</b>\n\n"
            + f"<b>⚙️ Процессор · {m['vcpus']} vCPU</b>\n{meter(m['cpu_percent'])}\n\n"
            + f"<b>🧠 Оперативная память</b>\n{meter(m['memory']['percent'])}\n"
            + f"{size(m['memory']['used'])} из {size(m['memory']['total'])}\n\n"
            + f"<b>💾 Накопитель</b>\n{meter(m['disk']['percent'])}\n"
            + f"{size(m['disk']['used'])} из {size(m['disk']['total'])}\n\n"
            + "<b>📈 Система</b>\n"
            + f"Средняя нагрузка (1/5/15 мин): {safe(load_avg)}\n"
            + f"⏱ Без перезагрузки: {uptime(m['uptime_seconds'])}\n\n"
            + f"<i>🕒 Проверено: {samara_time()}</i>")


def specs(server: Server, result: dict) -> str:
    title = f"⚙️ <b>ХАРАКТЕРИСТИКИ VPS</b>\n{safe(server.name)}\n{SEPARATOR}\n"
    if not server.enabled:
        return title + "\n⏸ Мониторинг отключён"
    if not result.get("ok"):
        return title + "\n⚠️ Характеристики недоступны"
    d = result["data"]
    return (title + "\n<b>🖥 Система</b>\n"
            + f"🏷 Имя: {safe(d['hostname'])}\n"
            + f"🐧 ОС: {safe(d['os'])}\n"
            + f"🧩 Ядро: {safe(d['kernel'])}\n"
            + f"🏗 Архитектура: {safe(d['architecture'])}\n\n"
            + "<b>⚙️ Оборудование</b>\n"
            + f"🖥 CPU: {safe(d['cpu_model'])}\n"
            + f"⚙️ vCPU: {d['vcpus']}\n"
            + f"🧠 Память: {size(d['memory']['total'])}\n"
            + f"💾 Диск: {size(d['disk']['total'])}\n\n"
            + "<b>🌐 Сеть</b>\n"
            + (f"📡 Интерфейс: {safe(d.get('network', {}).get('interface'))}\n"
               f"⬇️ Получено: {size(d['network']['rx'])}\n"
               f"⬆️ Отправлено: {size(d['network']['tx'])}\n"
               if d.get('network', {}).get('rx') is not None and
                  d.get('network', {}).get('tx') is not None else
               "⚪ Сетевые счётчики недоступны\n")
            + "\n<i>Только внешний интерфейс VPS, без повторного подсчёта "
              "Docker/VPN. Счётчики с момента запуска интерфейса, не тарифный лимит.</i>\n\n"
            + f"<i>🕒 Проверено: {samara_time()}</i>")


def services_text(server: Server, result: dict) -> str:
    title = f"🧩 <b>СЛУЖБЫ VPS</b>\n{safe(server.name)}\n{SEPARATOR}\n"
    if not server.enabled:
        return title + "\n⏸ Мониторинг отключён"
    if not result.get("ok"):
        return title + "\n⚠️ Нет данных о службах"
    states = result["data"].get("services", {})
    if not states:
        return title + "\nℹ️ Службы для проверки не указаны"
    icons = {"active": "🟢", "inactive": "⚪", "failed": "🔴",
             "activating": "🟡", "deactivating": "🟡"}
    labels = {"active": "Работает", "inactive": "Не запущена",
              "failed": "Ошибка", "activating": "Запускается",
              "deactivating": "Останавливается"}
    entries = [f"{icons.get(state, '❔')} <b>{safe(name)}</b> — {safe(labels.get(state, state))}"
               for name, state in states.items()]
    return (title + "\n" + "\n\n".join(entries) +
            "\n\n<i>⚪ Резервные протоколы могут работать отдельно или в контейнерах.</i>\n\n"
            + f"<i>🕒 Проверено: {samara_time()}</i>")


def billing_text(servers: tuple[Server, ...], source: str = "config.yaml",
                 status: str | None = None, auto_prolong: bool | None = None,
                 error: str | None = None) -> str:
    parts = ["📅 <b>АРЕНДА VPS</b>",
             f"🔗 Источник: {safe(source)}", SEPARATOR]
    for s in servers:
        parts.extend(["", f"{safe(s.name)}", "",
                      "<b>📆 Оплаченный период</b>",
                      f"⏳ Осталось: <b>{safe(remaining_text(s))}</b>",
                      (f"📅 Дата окончания: <b>{s.expires_at:%d.%m.%Y}</b>"
                       if s.expires_at else "📅 Дата окончания: <b>неизвестна</b>"),
                      "", "<b>💳 Тариф и оплата</b>",
                      f"🏢 Провайдер: {safe(s.provider)}",
                      f"🏷 Тариф: {safe(s.tariff)}",
                      f"💶 Стоимость: <b>{safe(s.price)}</b>"])
    if status is not None or auto_prolong is not None:
        parts.extend(["", "<b>🛠 Управление арендой</b>"])
    if status is not None:
        statuses = {"active": "🟢 Активна", "suspended": "🔴 Приостановлена",
                    "disabled": "🔴 Отключена", "expired": "🔴 Истекла",
                    "pending": "🟡 Ожидает активации"}
        parts.append("📡 Статус: " + safe(statuses.get(status.lower(), status)))
    if auto_prolong is not None:
        parts.append("🔁 Автопродление: " + ("🟢 Включено" if auto_prolong else "🔕 Выключено"))
    if error:
        parts.extend(["", f"⚠️ {safe(error)}"])
    parts.extend(["", f"<i>🕒 Показано: {samara_time()}</i>"])
    return "\n".join(parts)
