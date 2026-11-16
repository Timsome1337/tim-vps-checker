"""Telegram inline keyboards, separated from the message handlers."""
from __future__ import annotations

from typing import Protocol
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config, Server
from .alert_preferences import AlertPreferences, CATEGORIES, TITLES


class _KeyboardContext(Protocol):
    config: Config
    preferences: AlertPreferences


def root_keyboard(ctrl: _KeyboardContext) -> InlineKeyboardMarkup:
    servers = ctrl.config.servers
    if len(servers) == 1:
        sid = servers[0].id
        rows = [
            [InlineKeyboardButton('📊 Нагрузка', callback_data=f'status:{sid}'),
             InlineKeyboardButton('📅 Аренда', callback_data='billing')],
            [InlineKeyboardButton('⚙️ Параметры', callback_data=f'specs:{sid}'),
             InlineKeyboardButton('❓ Справка', callback_data='help')],
            [InlineKeyboardButton('📈 Статистика', callback_data=f'stats:{sid}'),
             InlineKeyboardButton('💾 Диск', callback_data=f'disk:{sid}')],
            [InlineKeyboardButton('🛠 Управление', callback_data='control'),
             InlineKeyboardButton('🔄 Обновить', callback_data='refresh')],
            [InlineKeyboardButton('🧪 Проверка', callback_data='diag'),
             InlineKeyboardButton('🔔 Уведомления', callback_data='alerts')],
        ]
    else:
        rows = [[InlineKeyboardButton(f'🖥️ {s.name}', callback_data=f'status:{s.id}')]
                for s in servers]
        rows += [[InlineKeyboardButton('📅 Аренда', callback_data='billing')],
                 [InlineKeyboardButton('❓ Справка', callback_data='help')],
                 [InlineKeyboardButton('📈 Статистика', callback_data=f'stats:{servers[0].id}')],
                 [InlineKeyboardButton('🔄 Обновить', callback_data='refresh')]]
    return InlineKeyboardMarkup(rows)



def server_keyboard(server: Server, current: str = 'status') -> InlineKeyboardMarkup:
    """Four buttons instead of six: refresh stays inside the current section."""
    sid = server.id
    if current not in ('status', 'specs', 'services'):
        current = 'status'
    neighbours = {
        'status': [('⚙️ Параметры', f'specs:{sid}'), ('🧩 Службы', f'services:{sid}')],
        'specs': [('📊 Нагрузка', f'status:{sid}'), ('🧩 Службы', f'services:{sid}')],
        'services': [('📊 Нагрузка', f'status:{sid}'), ('⚙️ Параметры', f'specs:{sid}')],
    }[current]
    rows = [
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'{current}:{sid}'),
         InlineKeyboardButton('🏠 Главная', callback_data='overview')],
        [InlineKeyboardButton(label, callback_data=target) for label, target in neighbours],
    ]
    if current == 'specs':
        rows.append([InlineKeyboardButton('🌐 Сеть и трафик', callback_data=f'network:{sid}')])
    return InlineKeyboardMarkup(rows)



def billing_keyboard(ctrl: _KeyboardContext) -> InlineKeyboardMarkup:
    if len(ctrl.config.servers) != 1:
        return root_keyboard(ctrl)
    sid = ctrl.config.servers[0].id
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Обновить', callback_data='billing'),
         InlineKeyboardButton('🏠 Главная', callback_data='overview')],
        [InlineKeyboardButton('📊 Нагрузка', callback_data=f'status:{sid}'),
         InlineKeyboardButton('⚙️ Параметры', callback_data=f'specs:{sid}')],
    ])


def network_keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'network:{sid}'),
         InlineKeyboardButton('⚙️ Параметры', callback_data=f'specs:{sid}')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def disk_keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'disk:{sid}'),
         InlineKeyboardButton('📈 Статистика', callback_data=f'stats:{sid}')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Перезагрузка', callback_data='ctl:reboot'),
         InlineKeyboardButton('💳 Продление', callback_data='ctl:renew')],
        [InlineKeyboardButton('📜 История действий', callback_data='actions')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])



def stats_keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📈 24 ч', callback_data=f'chart:{sid}:24h'),
         InlineKeyboardButton('📈 7 дн.', callback_data=f'chart:{sid}:7d'),
         InlineKeyboardButton('📈 30 дн.', callback_data=f'chart:{sid}:30d')],
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'stats:{sid}'),
         InlineKeyboardButton('📜 События', callback_data=f'events:{sid}')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def chart_keyboard(sid: str, period: str) -> InlineKeyboardMarkup:
    """Refresh the current graph instead of taking the user back to text statistics."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📈 24 ч', callback_data=f'chart:{sid}:24h'),
         InlineKeyboardButton('📈 7 дн.', callback_data=f'chart:{sid}:7d'),
         InlineKeyboardButton('📈 30 дн.', callback_data=f'chart:{sid}:30d')],
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'chart:{sid}:{period}'),
         InlineKeyboardButton('📊 Статистика', callback_data=f'stats:{sid}')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def events_keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Обновить', callback_data=f'events:{sid}'),
         InlineKeyboardButton('📈 Статистика', callback_data=f'stats:{sid}')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🧪 Проверка', callback_data='diag'),
         InlineKeyboardButton('🔔 Уведомления', callback_data='alerts')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])



def alert_keyboard(ctrl: _KeyboardContext, uid: int) -> InlineKeyboardMarkup:
    rows = []
    for name in CATEGORIES:
        flag = '🟢' if ctrl.preferences.enabled(uid, name) else '⚪'
        # Exact one normal space after the leading emoji on every Telegram button.
        rows.append([InlineKeyboardButton(f'{flag} {TITLES[name]}', callback_data=f'alert:{name}')])
    rows.append([InlineKeyboardButton('🧪 Тест уведомлений', callback_data='alerts:test')])
    rows.append([InlineKeyboardButton('🏠 Главная', callback_data='overview')])
    return InlineKeyboardMarkup(rows)

def diag_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Проверить снова', callback_data='diag'),
         InlineKeyboardButton('🔔 Уведомления', callback_data='alerts')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])


def reboot_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Подтвердить перезагрузку',
                              callback_data=f'ctl:confirm:{token}')],
        [InlineKeyboardButton('↩️ Отмена', callback_data='control')],
    ])


def renewal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🌐 Продлить в Aeza', url='https://my.aeza.net/')],
        [InlineKeyboardButton('↩️ Управление', callback_data='control')],
    ])


def actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔄 Обновить', callback_data='actions'),
         InlineKeyboardButton('↩️ Управление', callback_data='control')],
        [InlineKeyboardButton('🏠 Главная', callback_data='overview')],
    ])
