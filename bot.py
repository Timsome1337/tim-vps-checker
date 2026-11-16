#!/usr/bin/env python3
"""Private Telegram UI + background VPS health checks (Python 3.10+)."""
from __future__ import annotations

import asyncio
from io import BytesIO
from dataclasses import replace
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.aeza import AezaClient, AezaInfo
from app.control import owner_allowed, prepare, consume, reserve_action
from app.smart_alerts import evaluate_smart
from app.config import Config, Server, load_config
from app.formatting import billing_text, dashboard_text, full_status, services_text, short_status, specs
from app.probe import probe
from app.state import StateStore
from app.history import HistoryStore, PERIODS, fmt_time, summary, russian_measurements
from app.charts import draw_chart
from app.formatting import safe, percent, size
from app.alert_preferences import AlertPreferences, CATEGORIES, TITLES, category
from app.keyboards import (root_keyboard, server_keyboard, billing_keyboard,
                           network_keyboard, disk_keyboard, control_keyboard,
                           stats_keyboard, chart_keyboard, events_keyboard,
                           help_keyboard, alert_keyboard, diag_keyboard,
                           reboot_confirm_keyboard, renewal_keyboard, actions_keyboard)
from app.recovery import recovery_status
from app.maintenance import verify_sqlite

LOG = logging.getLogger('vps_monitor')
SAMARA_TZ = ZoneInfo('Europe/Samara')


@dataclass
class Controller:
    config: Config
    allowed_user_ids: tuple[int, ...]
    state: StateStore
    history: HistoryStore
    cache: dict[str, tuple[float, dict]] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    aeza: AezaClient | None = None
    aeza_cache: tuple[float, AezaInfo | None, str | None] | None = None
    aeza_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict = field(default_factory=dict)
    preferences: AlertPreferences = field(default_factory=lambda: AlertPreferences(Path("data/alert_preferences.sqlite3")))

    async def billing(self, refresh: bool = False) -> tuple[AezaInfo | None, str | None]:
        if not self.aeza:
            return None, None
        async with self.aeza_lock:
            if not refresh and self.aeza_cache and time.monotonic() - self.aeza_cache[0] < 180:
                return self.aeza_cache[1], self.aeza_cache[2]
            try:
                info = await asyncio.to_thread(self.aeza.read_service)
                error = None
            except Exception:
                # Provider failures must not break local VPS monitoring or reveal secrets.
                LOG.exception('Aeza API request failed')
                info, error = None, 'Aeza API временно недоступен'
            self.aeza_cache = time.monotonic(), info, error
            return info, error

    @staticmethod
    def merged(server: Server, info: AezaInfo | None) -> Server:
        if not info:
            return server
        return replace(server, expires_at=info.expires_at or server.expires_at,
                       tariff=info.tariff or server.tariff,
                       price=info.price or server.price)

    async def sample(self, server: Server, refresh: bool = False) -> dict:
        if not server.enabled:
            return {'ok': False, 'error': 'Мониторинг отключён'}
        async with self.locks.setdefault(server.id, asyncio.Lock()):
            entry = self.cache.get(server.id)
            if not refresh and entry and time.monotonic() - entry[0] < 25:
                return entry[1]
            try:
                result = await asyncio.to_thread(probe, server)
            except Exception:
                # A probe exception becomes a failed *sample*, not a failed bot job.
                LOG.exception('Unexpected probe error for %s', server.id)
                result = {'ok': False, 'error': 'Не удалось собрать показатели'}
            self.cache[server.id] = (time.monotonic(), result)
            return result

    def find_server(self, sid: str) -> Server | None:
        return next((s for s in self.config.servers if s.id == sid), None)


def controller(context: ContextTypes.DEFAULT_TYPE) -> Controller:
    return context.application.bot_data['controller']


def authorized(update: Update, ctrl: Controller) -> bool:
    return (update.effective_user is not None
            and update.effective_user.id in ctrl.allowed_user_ids
            and update.effective_chat is not None
            and update.effective_chat.type == 'private')


async def alerts_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    uid = update.effective_user.id
    text = ('🔔 <b>УВЕДОМЛЕНИЯ</b>\n'
            '☁️ Aeza · VPS\n'
            '────────────\n\n'
            'Нажми на категорию для переключения.\n'
            '🟢 Включено · ⚪ Выключено\n'
            '<i>Настройки только для этого аккаунта.</i>')
    recent = []
    for name in CATEGORIES:
        ts = await asyncio.to_thread(ctrl.preferences.last_sent, uid, name)
        if ts:
            recent.append(f'{safe(TITLES[name])}: {fmt_time(ts)}')
    if recent:
        text += '\n\n<b>Последние доставленные предупреждения</b>\n' + '\n'.join(recent)
    else:
        text += '\n\n<i>Автоматических предупреждений пока не было.</i>'
    await respond(update, text, alert_keyboard(ctrl, uid))


async def test_alerts_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explicit per-user delivery test; never broadcasts to other accounts."""
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    uid = update.effective_user.id
    await context.bot.send_message(
        chat_id=uid,
        text='🧪 Тест уведомлений Tim VPS Checker\n'
             '✅ Сообщение доставлено по запросу этого аккаунта.\n'
             'Это проверка доставки, а не реальная неисправность VPS.')
    if update.callback_query:
        await alerts_view(update, context)


async def diag_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    lines = ['🧪 <b>САМОДИАГНОСТИКА</b>', '🖥️ VPS · мониторинг', '────────────', '']
    try:
        await context.bot.get_me()
        lines.append('🟢 Telegram API — доступен')
    except TelegramError:
        lines.append('🔴 Telegram API — ошибка запроса')
    if ctrl.aeza:
        info, err = await ctrl.billing(refresh=True)
        lines.append('🟢 Aeza API — отвечает' if info and not err else '🟠 Aeza API — нет подтверждённых данных')
    else:
        lines.append('⚪ Aeza API — не настроен')
    server = selected_server(ctrl)
    if server:
        try:
            sample = await ctrl.sample(server, refresh=True)
            lines.append('🟢 Сбор показателей — работает' if sample.get('ok') else
                         '🔴 Сбор показателей — ошибка')
            disk = sample.get('data', {}).get('disk', {}) if sample.get('ok') else {}
            free = int(disk.get('total', 0)) - int(disk.get('used', 0))
            if disk and free >= 0:
                state = '🟢' if free >= 1_000_000_000 else '⚠️'
                lines.append(f'{state} Свободно на диске: {size(free)}')
        except Exception:
            lines.append('🔴 Сбор показателей — ошибка')
        try:
            rows = await asyncio.to_thread(ctrl.history.samples, server.id, '24h')
            last = rows[-1]['ts'] if rows else None
            db_ok, _ = await asyncio.to_thread(verify_sqlite, ctrl.history.path)
            if not db_ok:
                lines.append('🔴 SQLite — ошибка целостности')
            elif last and time.time() - last < max(900, ctrl.config.check_interval_seconds * 3):
                lines.append('🟢 SQLite — история обновляется')
            elif last:
                lines.append('🟠 SQLite — последняя запись устарела')
            else:
                lines.append('🟠 SQLite — пока нет измерений')
        except Exception:
            lines.append('🔴 SQLite — не удалось прочитать историю')
    else:
        lines.append('🔴 Сервер не настроен')
    journal = Path('/etc/systemd/journald.conf.d/70-tim-vps-checker.conf')
    try:
        conf = journal.read_text(encoding='utf-8')
        lines.append('🟢 journald — ограничен 150 МБ' if 'SystemMaxUse=150M' in conf else
                     '🟠 journald — проверь настройку лимита')
    except OSError:
        lines.append('🟠 journald — конфигурация не прочитана')
    recovered, label = await asyncio.to_thread(recovery_status)
    lines.append(('🟢' if recovered is True else '🟡' if recovered is None else '⚠️')
                 + ' systemd — ' + safe(label))
    lines.append('\n<i>Проверка локальная: при остановке VPS бот не сможет оповестить.</i>')
    await respond(update, '\n'.join(lines), diag_keyboard())

async def respond(update: Update, message: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    # Rich Telegram HTML is composed by app.formatting, which escapes all dynamic values.
    # Use edit_message_text for buttons to keep the chat free of duplicate cards.
    if update.callback_query:
        if getattr(update.callback_query.message, 'photo', None):
            # A Telegram photo cannot be edited into a text message.
            await update.callback_query.message.reply_text(
                message, reply_markup=keyboard, parse_mode='HTML', disable_web_page_preview=True)
            try:
                await update.callback_query.message.delete()
            except TelegramError:
                pass
            return
        try:
            await update.callback_query.edit_message_text(
                message, reply_markup=keyboard, parse_mode='HTML',
                disable_web_page_preview=True)
        except BadRequest as exc:
            reason = str(exc).lower()
            if 'not modified' in reason:
                return
            if any(fault in reason for fault in (
                'message to edit not found', 'message can\'t be edited',
                'message is not accessible', 'message_id_invalid',
                'message identifier is not specified')):
                # Older Telegram cards can no longer be edited; send one fresh card.
                await update.callback_query.message.reply_text(
                    message, reply_markup=keyboard, parse_mode='HTML',
                    disable_web_page_preview=True)
                return
            raise
    elif update.message:
        await update.message.reply_text(message, reply_markup=keyboard,
                                        parse_mode='HTML', disable_web_page_preview=True)


async def overview(update: Update, context: ContextTypes.DEFAULT_TYPE, refresh: bool = False) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    statuses, (info, _) = await asyncio.gather(
        asyncio.gather(*(ctrl.sample(s, refresh=refresh) for s in ctrl.config.servers)),
        ctrl.billing(refresh=refresh))
    if len(ctrl.config.servers) == 1:
        server = ctrl.merged(ctrl.config.servers[0], info)
        message = dashboard_text(server, statuses[0])
    else:
        lines = ['🛰 <b>TIM VPS CHECKER</b>', '📡 Мониторинг VPS',
                 '━━━━━━━━━━━━━━', '']
        for server, result in zip(ctrl.config.servers, statuses):
            lines.extend([short_status(server, result), ''])
        message = '\n'.join(lines).rstrip()
    await respond(update, message, root_keyboard(ctrl))


async def detail(update: Update, context: ContextTypes.DEFAULT_TYPE,
                 kind: str = 'status', sid: str | None = None) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    if sid is None:
        live = [s for s in ctrl.config.servers if s.enabled]
        if len(live) != 1:
            await overview(update, context)
            return
        server = live[0]
    else:
        server = ctrl.find_server(sid)
    if not server:
        await respond(update, 'Сервер не найден. Проверьте config.yaml.', root_keyboard(ctrl))
        return
    result = await ctrl.sample(server, refresh=True)
    info, _ = await ctrl.billing()
    effective = ctrl.merged(server, info)
    renderer = {'status': full_status, 'specs': specs, 'services': services_text}[kind]
    await respond(update, renderer(effective, result), server_keyboard(server, kind))


def selected_server(ctrl: Controller, sid: str | None = None) -> Server | None:
    if sid:
        return ctrl.find_server(sid)
    return next((s for s in ctrl.config.servers if s.enabled), None)


async def network_view(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str | None = None) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    server = selected_server(ctrl, sid)
    if not server:
        return
    result = await ctrl.sample(server, refresh=True)
    title = f'🌐 <b>СЕТЬ И ТРАФИК</b>\n{safe(server.name)}\n────────────\n\n'
    if not result.get('ok'):
        await respond(update, title + '⚠️ Сетевые показатели недоступны.', network_keyboard(server.id))
        return
    n = result['data'].get('network') or {}
    if not n.get('interface') or n.get('rx') is None:
        await respond(update, title + '❔ Не удалось определить основной интерфейс.', network_keyboard(server.id))
        return
    def rate(value: float | None) -> str:
        return ('нет измерения' if value is None
                else f'{float(value):.2f}'.replace('.', ',') + ' Мбит/с')
    day = await asyncio.to_thread(ctrl.history.traffic_today, server.id)
    text = (title + f'📡 Интерфейс: <b>{safe(n["interface"])}</b>\n\n'
            '<b>⚡ Текущая скорость</b> (примерно за 0,8 сек)\n'
            f'⬇️ Приём: <b>{rate(n.get("rx_mbps"))}</b>\n'
            f'⬆️ Отправка: <b>{rate(n.get("tx_mbps"))}</b>\n\n'
            '<b>📆 Сегодня (Самара, с 00:00)</b>\n')
    if day['intervals']:
        text += (f'⬇️ Получено: <b>{size(day["rx"])}</b>\n'
                 f'⬆️ Отправлено: <b>{size(day["tx"])}</b>\n'
                 f'🧾 Учтено интервалов: {day["intervals"]}\n')
    else:
        text += '⏳ Недостаточно сохранённых замеров.\n'
    if day['gaps']:
        text += f'⚠️ Пропущено/сброшено интервалов: {day["gaps"]}\n'
    text += ('\n<i>Скорость — короткий замер. Суточный трафик — только '
             'учтённые интервалы, не данные биллинга Aeza.</i>')
    await respond(update, text, network_keyboard(server.id))


async def disk_view(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str | None = None) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    server = selected_server(ctrl, sid)
    if not server:
        return
    result = await ctrl.sample(server, refresh=True)
    if not result.get('ok'):
        await respond(update, '💾 <b>ДИСК VPS</b>\n\n⚠️ Нет данных о диске.', disk_keyboard(server.id))
        return
    disk = result['data']['disk']
    used, total = int(disk['used']), int(disk['total'])
    free = max(0, total - used)
    rows = await asyncio.to_thread(ctrl.history.samples, server.id, '24h')
    stats = summary(rows)
    change = stats.get('disk_used_delta_bytes')
    trend = ('⏳ История ещё накапливается' if change is None else
             ('🧹 Освобождено: ' + size(abs(change)) if change < 0 else
              '📦 Занято дополнительно: ' + size(change) if change > 0 else
              '📌 Использование диска без изменений'))
    warning = '⚠️ Места осталось мало' if free < 1_000_000_000 else '🟢 Запас места достаточный'
    msg = (f'💾 <b>ДИСК VPS</b>\n{safe(server.name)}\n────────────\n\n'
           f'{warning}\n\n'
           f'📦 Всего: <b>{size(total)}</b>\n'
           f'📊 Занято: <b>{size(used)}</b> ({percent(disk["percent"])})\n'
           f'🟢 Свободно: <b>{size(free)}</b>\n\n'
           f'{trend}\n'
           '<i>Динамика — за доступный период измерений.</i>')
    await respond(update, msg, disk_keyboard(server.id))


def control_owner(ctrl: Controller, update: Update) -> bool:
    if not authorized(update, ctrl):
        return False
    raw_ids = os.getenv('TELEGRAM_CONTROL_IDS', '').strip()
    parts = [part.strip() for part in raw_ids.split(',')]
    if not parts or not all(part.isdecimal() and int(part) > 0 for part in parts):
        return False
    return update.effective_user.id in {int(part) for part in parts}


async def control_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not control_owner(ctrl, update):
        return
    info, _ = await ctrl.billing(refresh=True)
    status = safe(info.status) if info and info.status else 'Неизвестно'
    expiry = info.expires_at.strftime('%d.%m.%Y') if info and info.expires_at else 'Неизвестно'
    sid = safe(os.getenv('AEZA_SERVICE_ID', 'не указан'))
    msg = (f'🛠 <b>УПРАВЛЕНИЕ VPS</b>\n☁️ Aeza · услуга {sid}\n'
           '────────────\n\n'
           f'🟢 Статус: <b>{status}</b>\n'
           f'📅 Аренда до: <b>{expiry}</b>\n\n'
           '<i>🔄 Перезагрузка — с подтверждением.\n'
           '💳 Продление — через кабинет Aeza.</i>')
    await respond(update, msg, control_keyboard())


async def reboot_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not control_owner(ctrl, update):
        return
    sid = os.getenv('AEZA_SERVICE_ID', '').strip()
    if (not ctrl.aeza or not sid.isdecimal() or
            ctrl.aeza.service_id != sid or not ctrl.aeza.base_url.endswith('/api/v2')):
        await respond(update, '⚠️ Для перезагрузки нужен Aeza API v2 и точный AEZA_SERVICE_ID.', control_keyboard())
        return
    if os.getenv('AEZA_ALLOW_REBOOT', '0') != '1':
        await respond(update, '🔒 Перезагрузка пока выключена в .env.\n'
                      'Включить: AEZA_ALLOW_REBOOT=1\n\n'
                      'Для команды нужен API-ключ с правом управления VPS.', control_keyboard())
        return
    token = prepare(ctrl.pending, update.effective_user.id, sid)
    keyboard = reboot_confirm_keyboard(token)
    await respond(update,
                  '⚠️ <b>ПОДТВЕРЖДЕНИЕ</b>\n\n'
                  f'🔄 Перезагрузить VPS Aeza (услуга {safe(sid)})?\n\n'
                  'VPN и этот Telegram-бот временно отключатся. '
                  'Действие может прервать текущие подключения.\n\n'
                  '<i>Подтверждение действует 90 секунд.</i>', keyboard)


async def reboot_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    ctrl = controller(context)
    if not control_owner(ctrl, update):
        return
    uid = update.effective_user.id
    sid = os.getenv('AEZA_SERVICE_ID', '').strip()
    if not consume(ctrl.pending, uid, sid, token):
        await respond(update, '⌛ Подтверждение недействительно или истекло.', control_keyboard())
        return
    if os.getenv('AEZA_ALLOW_REBOOT', '0') != '1' or not ctrl.aeza:
        await respond(update, '🔒 Перезагрузка отключена.', control_keyboard())
        return
    async with ctrl.action_lock:
        # Reservation persists across reboot. Never automatically repeat HTTP POST.
        try:
            allowed = await asyncio.to_thread(reserve_action, Path('data/last_reboot_request.json'), sid)
        except Exception:
            LOG.exception('Cannot reserve reboot attempt')
            await respond(update, '⚠️ Не удалось сохранить защиту от повторной перезагрузки.', control_keyboard())
            return
        if not allowed:
            await respond(update, '⏳ Недавно уже запрашивалась перезагрузка. '
                          'Повтор заблокирован на 30 минут.', control_keyboard())
            return
        # Persist who requested the operation before sending anything to Aeza.
        try:
            audit_id = await asyncio.to_thread(ctrl.history.start_control_action,
                                                ctrl.config.servers[0].id, uid, 'reboot')
        except Exception:
            LOG.exception('Cannot audit reboot; aborting remote action')
            await respond(update, '⚠️ Журнал действий недоступен: перезагрузка отменена.', control_keyboard())
            return
        # Acknowledge before initiating restart: bot may disappear mid-response.
        await respond(update, '🔄 Запрос перезагрузки отправляется в Aeza.\n'
                      'Сервер и Telegram-бот могут быть временно недоступны.\n'
                      'Не нажимай повторно, пока не проверишь состояние VPS.', control_keyboard())
        try:
            await asyncio.to_thread(ctrl.aeza.restart_once, sid)
            # The response confirms request acceptance, not a completed OS reboot.
            await asyncio.to_thread(ctrl.history.finish_control_action, audit_id,
                                    'accepted', 'Запрос принят Aeza; завершение не подтверждено')
        except (RuntimeError, ValueError) as exc:
            try:
                await asyncio.to_thread(ctrl.history.finish_control_action, audit_id,
                                        'uncertain', 'Aeza не подтвердила результат запроса')
            except Exception:
                LOG.exception('Could not update audit result')
            LOG.warning('Aeza restart result unclear: %s', type(exc).__name__)
            try:
                await respond(update, '⚠️ Aeza не подтвердила перезагрузку.\n'
                              f'{safe(exc)}\n'
                              'Проверь состояние в личном кабинете перед повторной попыткой.',
                              control_keyboard())
            except TelegramError:
                pass


async def renewal_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not control_owner(ctrl, update):
        return
    info, _ = await ctrl.billing(refresh=True)
    expiry = info.expires_at.strftime('%d.%m.%Y') if info and info.expires_at else 'неизвестно'
    server = ctrl.merged(ctrl.config.servers[0], info)
    try:
        audit_id = await asyncio.to_thread(ctrl.history.start_control_action,
                                            ctrl.config.servers[0].id,
                                            update.effective_user.id, 'renewal_page')
        await asyncio.to_thread(ctrl.history.finish_control_action, audit_id,
                                'page_shown', 'Экран продления открыт; оплата не проверялась')
    except Exception:
        LOG.exception('Could not audit renewal page')
    keyboard = renewal_keyboard()
    text = (f'💳 <b>ПРОДЛЕНИЕ VPS</b>\n☁️ Aeza · {safe(server.tariff)}\n'
            '────────────\n\n'
            f'📅 Оплачено до: <b>{expiry}</b>\n'
            f'💶 Обычная стоимость: <b>{safe(server.price)}</b>\n\n'
            '<i>Оплата проходит в кабинете Aeza после проверки суммы и срока. '
            'Бот деньги не списывает.</i>')
    await respond(update, text, keyboard)


async def actions_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not control_owner(ctrl, update):
        return
    server = selected_server(ctrl)
    if not server:
        return
    try:
        records = await asyncio.to_thread(ctrl.history.recent_control_actions, server.id, 10)
    except Exception:
        await respond(update, '⚠️ История действий временно недоступна.', control_keyboard())
        return
    lines = ['📜 <b>ДЕЙСТВИЯ С VPS</b>', '☁️ Aeza · VPS', '────────────', '']
    if not records:
        lines.append('ℹ️ Управляющих запросов пока не было.')
    for rec in records:
        label = '🔄 Перезагрузка' if rec['action'] == 'reboot' else '💳 Экран продления'
        state = {'accepted': 'запрос принят', 'uncertain': 'результат неизвестен',
                 'requested': 'ожидает ответа', 'page_shown': 'открыт экран',
                 'failed': 'ошибка'}.get(rec['status'], 'неизвестно')
        lines.append(f'{label} · {fmt_time(rec["ts"])}')
        lines.append(f'Аккаунт: <code>{int(rec["actor_id"])}</code> · {state}')
        lines.append('')
    lines.append('<i>«Запрос принят» не означает, что VPS уже перезагрузился. '
                 'Оплата в Aeza здесь не подтверждается.</i>')
    await respond(update, '\n'.join(lines), actions_keyboard())


async def stats_view(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str | None = None) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    server = selected_server(ctrl, sid)
    if server is None:
        await respond(update, '⚠️ Сервер не найден.', root_keyboard(ctrl))
        return
    rows = await asyncio.to_thread(ctrl.history.samples, server.id, '24h')
    stats = summary(rows)
    text = (f'📈 <b>СТАТИСТИКА VPS</b>\n{safe(server.name)}\n'
            '━━━━━━━━━━━━━━\n\n')
    if not stats['count']:
        text += ('🕒 Пока нет сохранённых измерений.\n'
                 'Первая запись появится после ближайшей фоновой проверки.\n')
    else:
        delta = stats.get('disk_used_delta_bytes')
        delta_line = ('\n🧹 Освобождено: ' + size(abs(delta)) if delta < 0 else
                      '\n💾 Занято дополнительно: ' + size(delta)) if delta is not None else ''
        text += (f'📊 <b>За 24 часа</b> · {russian_measurements(stats["count"])}\n'
                 f'✅ Успешных записей: {stats["count"]} из {stats["total"]}\n\n'
                 f'⚙️ CPU · ср. {percent(stats["cpu_avg"])} · '
                 f'мин. {percent(stats["cpu_min"])} · пик {percent(stats["cpu_max"])}\n'
                 f'🧠 ОЗУ · ср. {percent(stats["memory_avg"])} · '
                 f'мин. {percent(stats["memory_min"])} · пик {percent(stats["memory_max"])}\n'
                 f'💾 Диск · ср. {percent(stats["disk_avg"])} · '
                 f'мин. {percent(stats["disk_min"])} · пик {percent(stats["disk_max"])}'
                 f'{delta_line}\n'
                 f'\n🕒 Последний замер: {fmt_time(stats["last_ts"])} (Самара)\n')
    if rows:
        text += f'\n<i>Данные с {fmt_time(rows[0]["ts"])} · Самара.</i>'
    text += (f'\n<i>Замер каждые {ctrl.config.check_interval_seconds // 60} мин.</i>')
    await respond(update, text, stats_keyboard(server.id))


async def events_view(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str | None = None) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    server = selected_server(ctrl, sid)
    if server is None:
        await respond(update, '⚠️ Сервер не найден.', root_keyboard(ctrl))
        return
    rows = await asyncio.to_thread(ctrl.history.recent_events, server.id)
    lines = [f'📜 <b>ИСТОРИЯ СОБЫТИЙ</b>\n{safe(server.name)}', '────────────', '']
    if not rows:
        lines.append('ℹ️ Событий пока не зарегистрировано.')
    for event in rows[:10]:
        mark = {'alert': '🔔', 'reboot': '🔄', 'gap': '⏳', 'service': '🧩', 'startup': '🚀'}.get(event['kind'], '📌')
        lines.append(f"{mark} <b>{fmt_time(event['ts'])}</b> · {safe(event['description'])}")
        lines.append('')
    lines.append('<i>Последние 10 событий · Самара. Пропуск ≠ отключение VPS.</i>')
    await respond(update, '\n'.join(lines), events_keyboard(server.id))


async def chart_view(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     sid: str, period: str) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    server = selected_server(ctrl, sid)
    if server is None or period not in PERIODS:
        return
    rows = await asyncio.to_thread(ctrl.history.samples, server.id, period)
    if not any(r.get('ok') for r in rows):
        await respond(update,
                      '📈 За выбранный период пока нет измерений.\n'
                      'История измерений пока пуста.',
                      stats_keyboard(server.id))
        return
    try:
        png = await asyncio.to_thread(draw_chart, rows, period, server.name)
    except Exception:
        LOG.exception('Chart rendering failed')
        await respond(update, '⚠️ Не удалось построить график. Сбор данных продолжает работать.',
                      stats_keyboard(server.id))
        return
    caption = ('📈 <b>Нагрузка VPS</b> · '
               + {'24h': '24 часа', '7d': '7 дней', '30d': '30 дней'}[period]
               + f'\n🕒 Самара · {russian_measurements(sum(bool(r.get("ok")) for r in rows))}')
    keyboard = chart_keyboard(server.id, period)
    query = update.callback_query
    if query and getattr(query.message, 'photo', None):
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=BytesIO(png), caption=caption, parse_mode='HTML'),
                reply_markup=keyboard)
            return
        except BadRequest:
            LOG.warning('Could not edit existing chart; sending a replacement')
    # Telegram cannot convert a text message directly to a photo.
    # Send first, then delete the old card if possible (failure is harmless).
    await context.bot.send_photo(chat_id=update.effective_chat.id,
                                 photo=BytesIO(png), caption=caption,
                                 parse_mode='HTML', reply_markup=keyboard)
    if query:
        try:
            await query.message.delete()
        except TelegramError:
            LOG.debug('Old card could not be deleted after sending chart')


async def disk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await disk_view(update, context, context.args[0] if context.args else None)


async def network_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await network_view(update, context, context.args[0] if context.args else None)


async def control_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await control_view(update, context)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await stats_view(update, context, context.args[0] if context.args else None)


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await events_view(update, context, context.args[0] if context.args else None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await overview(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await detail(update, context, 'status', context.args[0] if context.args else None)


async def specs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await detail(update, context, 'specs', context.args[0] if context.args else None)


async def services_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await detail(update, context, 'services', context.args[0] if context.args else None)


async def billing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if authorized(update, ctrl):
        info, error = await ctrl.billing(refresh=True)
        server = ctrl.merged(ctrl.config.servers[0], info)
        source = 'Aeza API' if info else 'config.yaml (вручную)'
        if not ctrl.aeza:
            error = 'Для автоматических данных укажите AEZA_API_KEY в .env'
        elif info and not info.expires_at:
            error = 'API вернула информацию об услуге, но дата оплаты не найдена в известных полях.'
        await respond(update, billing_text((server,), source=source,
                                           status=info.status if info else None,
                                           auto_prolong=info.auto_prolong if info else None,
                                           error=error), billing_keyboard(ctrl))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if authorized(update, ctrl):
        text = (
            '❓ <b>СПРАВКА · TIM VPS CHECKER</b>\n'
            '☁️ Aeza · VPS\n'
            '────────────\n\n'
            '<b>📊 Мониторинг</b>\n'
            '/start — главная\n'
            '/status — нагрузка\n'
            '/specs — характеристики\n'
            '/disk — диск\n'
            '/network — сеть\n'
            '/stats — графики\n\n'
            '<b>🛠 Управление и история</b>\n'
            '/billing — аренда\n'
            '/control — перезагрузка / продление\n'
            '/events — события\n\n'
            '<b>⚙️ Проверка и настройки</b>\n'
            '/services — службы\n'
            '/diag — самодиагностика\n'
            '/alerts — уведомления\n'
            '/testalerts — тест доставки\n'
            '/myid — Telegram ID\n\n'
            '<i>🕒 Время — Самара. Доступ — только трём аккаунтам.</i>'
        )
        await respond(update, text, help_keyboard())


async def testalerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await test_alerts_view(update, context)


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Safe first-run bootstrap: reveal the requesting user's ID to that user ONLY.
    if update.effective_chat and update.effective_chat.type == 'private' and update.effective_user and update.message:
        await update.message.reply_text(f'🪪 Твой Telegram ID: {update.effective_user.id}')


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    if not authorized(update, ctrl):
        return
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ''
    if data in ('overview', 'refresh'):
        await overview(update, context, refresh=data == 'refresh')
    elif data == 'billing':
        await billing_cmd(update, context)
    elif data == 'control':
        await control_view(update, context)
    elif data == 'actions':
        await actions_view(update, context)
    elif data == 'diag':
        await diag_view(update, context)
    elif data == 'help':
        await help_cmd(update, context)
    elif data == 'alerts':
        await alerts_view(update, context)
    elif data == 'alerts:test':
        await test_alerts_view(update, context)
    elif data.startswith('alert:'):
        name = data.split(':', 1)[1]
        if name in CATEGORIES:
            await asyncio.to_thread(ctrl.preferences.toggle, update.effective_user.id, name)
        await alerts_view(update, context)
    elif data == 'ctl:reboot':
        await reboot_preview(update, context)
    elif data == 'ctl:renew':
        await renewal_view(update, context)
    elif data.startswith('ctl:confirm:'):
        await reboot_confirm(update, context, data.split(':', 2)[2])
    elif data.startswith('chart:'):
        parts = data.split(':', 2)
        if len(parts) == 3:
            await chart_view(update, context, parts[1], parts[2])
    elif data.startswith('stats:'):
        await stats_view(update, context, data.partition(':')[2])
    elif data.startswith('events:'):
        await events_view(update, context, data.partition(':')[2])
    elif ':' in data:
        kind, sid = data.split(':', 1)
        if kind in ('status', 'specs', 'services'):
            await detail(update, context, kind, sid)
        elif kind == 'network':
            await network_view(update, context, sid)
        elif kind == 'disk':
            await disk_view(update, context, sid)


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = controller(context)
    info, _ = await ctrl.billing()
    for server in ctrl.config.servers:
        if not server.enabled:
            continue
        try:
            result = await ctrl.sample(server, refresh=True)
            old = ctrl.state.get(server.id)
            messages, next_state = evaluate_smart(ctrl.merged(server, info), result, old, ctrl.config,
                                                  today=datetime.now(SAMARA_TZ).date())
            # Persist incident state before sending, to avoid duplicate alarms after a crash.
            await asyncio.to_thread(ctrl.state.set, server.id, next_state)
            try:
                await asyncio.to_thread(ctrl.history.record, server.id, result, messages,
                                        ctrl.config.check_interval_seconds)
            except Exception:
                LOG.exception('History could not be saved; monitoring continues')
            for message in messages:
                group = category(message)
                for uid in ctrl.allowed_user_ids:
                    try:
                        if await asyncio.to_thread(ctrl.preferences.enabled, uid, group):
                            await context.bot.send_message(chat_id=uid, text=message)
                            await asyncio.to_thread(ctrl.preferences.mark_sent, uid, group)
                    except TelegramError:
                        LOG.exception('Could not deliver notification to %s', uid)
                    except Exception:
                        LOG.exception('Could not read local notification preferences')
        except Exception:
            # One failed server (or storage write) must not interrupt other servers.
            # Do not send unpersisted notifications if alert state could not be saved.
            LOG.exception('Scheduled monitor cycle failed for %s', server.id)


async def post_init(application: Application) -> None:
    # Each bot start is recorded locally, independent of a true OS reboot.
    ctrl = application.bot_data['controller']
    for server in ctrl.config.servers:
        try:
            await asyncio.to_thread(ctrl.history.add_event, server.id, 'startup',
                                    'Запущен процесс Tim VPS Checker. Это не обязательно перезагрузка VPS.')
        except Exception:
            LOG.exception('Could not record bot startup')
    await application.bot.set_my_commands([
        BotCommand('start', '🏠 Главная'),
        BotCommand('status', '📊 Нагрузка VPS'),
        BotCommand('specs', '⚙️ Характеристики'),
        BotCommand('services', '🧩 Службы'),
        BotCommand('billing', '📅 Оплата и аренда'),
        BotCommand('stats', '📈 Графики нагрузки'),
        BotCommand('disk', '💾 Диск и свободное место'),
        BotCommand('network', '🌐 Сеть и трафик'),
        BotCommand('control', '🛠 Перезагрузка и продление'),
        BotCommand('actions', '📜 История действий'),
        BotCommand('events', '📜 История событий'),
        BotCommand('diag', '🧪 Самодиагностика'),
        BotCommand('alerts', '🔔 Уведомления'),
        BotCommand('testalerts', '🧪 Проверка доставки'),
        BotCommand('myid', '🪪 Мой Telegram ID'),
        BotCommand('help', '❓ Справка'),
    ])


def main() -> None:
    load_dotenv()
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    user_ids = os.getenv('TELEGRAM_USER_IDS', os.getenv('TELEGRAM_USER_ID', '')).strip()
    if not token or token == 'PASTE_TOKEN_HERE':
        raise SystemExit('Set TELEGRAM_BOT_TOKEN in .env')
    ids = [value.strip() for value in user_ids.split(',')]
    if not ids or not all(value.isdecimal() and int(value) > 0 for value in ids):
        raise SystemExit('Set comma-separated numeric TELEGRAM_USER_IDS in .env')
    config = load_config(os.getenv('CONFIG_FILE', 'config.yaml'))
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    api_key = os.getenv('AEZA_API_KEY', '').strip()
    aeza = AezaClient(api_key,
                      base_url=os.getenv('AEZA_API_BASE', 'https://my.aeza.net/api/v2'),
                      service_id=os.getenv('AEZA_SERVICE_ID', '')) if api_key else None
    ctrl = Controller(config=config, allowed_user_ids=tuple(dict.fromkeys(int(uid) for uid in ids)),
                      state=StateStore(Path('data/state.json')),
                      history=HistoryStore(Path('data/history.sqlite3')), aeza=aeza)
    app = Application.builder().token(token).post_init(post_init).build()
    app.bot_data['controller'] = ctrl
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('status', status_cmd))
    app.add_handler(CommandHandler('specs', specs_cmd))
    app.add_handler(CommandHandler('services', services_cmd))
    app.add_handler(CommandHandler('billing', billing_cmd))
    app.add_handler(CommandHandler('stats', stats_cmd))
    app.add_handler(CommandHandler('disk', disk_cmd))
    app.add_handler(CommandHandler('network', network_cmd))
    app.add_handler(CommandHandler('control', control_cmd))
    app.add_handler(CommandHandler('actions', actions_view))
    app.add_handler(CommandHandler('events', events_cmd))
    app.add_handler(CommandHandler('diag', diag_view))
    app.add_handler(CommandHandler('alerts', alerts_view))
    app.add_handler(CommandHandler('testalerts', testalerts_cmd))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('myid', myid_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))
    if app.job_queue is None:
        raise SystemExit('Install python-telegram-bot[job-queue] from requirements.txt')
    app.job_queue.run_repeating(scheduled_check,
                                interval=config.check_interval_seconds,
                                first=10, name='vps-health-check')
    LOG.info('Starting Telegram VPS monitor; %s servers configured', len(config.servers))
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == '__main__':
    main()
