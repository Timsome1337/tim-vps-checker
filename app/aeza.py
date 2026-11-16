"""Aeza billing reader and explicitly gated VPS reboot adapter. Never log API secrets."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlparse

_ALLOWED_IDS = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_DATE_KEYS = ("expiresAt", "expiredAt", "expireAt", "expires_at", "expiryDate",
              "expirationDate", "expiration_at", "paidUntil", "paidTill", "paidTo",
              "nextPaymentAt", "nextBillingDate", "nextPaymentDate", "periodEnd",
              "endDate", "endsAt", "endTime", "expiry", "expires")


@dataclass(frozen=True)
class AezaInfo:
    service_id: str
    name: str | None = None
    expires_at: date | None = None
    tariff: str | None = None
    price: str | None = None
    status: str | None = None
    auto_prolong: bool | None = None
    payment_term: str | None = None


def parse_date(value: object) -> date | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (float, int)):
        try:
            timestamp = float(value)
            if timestamp > 10**12:  # milliseconds
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date() if timestamp > 10**8 else None
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return parse_date(int(s))
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _pick(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if value is not None and value != '':
            return value
    return None


def _nested_date(item: dict) -> date | None:
    for source in (item, item.get('billing'), item.get('payment'), item.get('subscription')):
        if not isinstance(source, dict):
            continue
        for key in _DATE_KEYS:
            d = parse_date(source.get(key))
            if d:
                return d
    return None


def _as_price(obj: object, parent: dict) -> str | None:
    if isinstance(obj, dict):
        amount = _pick(obj, 'value', 'amount', 'price')
        currency = _pick(obj, 'currency', 'currencyCode', 'suffix')
    else:
        amount = obj
        currency = _pick(parent, 'currency', 'currencyCode')
    if not isinstance(amount, (str, int, float)) or isinstance(amount, bool):
        return None
    v = str(amount).strip()
    if not v or len(v) >= 60:
        return None
    # Aeza numeric prices may be minor currency units; without an explicit unit
    # it is safer to show 'Не указана' than a misleading figure.
    if isinstance(currency, str) and 1 <= len(currency) < 16:
        return f'{v} {currency}'
    if isinstance(amount, str) and any(symbol in v for symbol in ('₽', '$', '€', 'RUB', 'USD', 'EUR')):
        return v
    return None


def decode_service(item: dict) -> AezaInfo:
    if not isinstance(item, dict):
        raise ValueError('Некорректная запись сервиса Aeza')
    sid = _pick(item, 'id', 'serviceId', 'uuid')
    if sid is None:
        raise ValueError('В ответе Aeza отсутствует ID услуги')
    product = item.get('product') if isinstance(item.get('product'), dict) else {}
    tariff = _pick(item, 'tariff', 'tariffName', 'planName') or _pick(product, 'name', 'title')
    amount = _pick(item, 'price', 'monthlyPrice', 'prolongPrice')
    autoprolong = _pick(item, 'autoProlong', 'autoRenew', 'autoRenewal')
    return AezaInfo(
        service_id=str(sid),
        name=_string(_pick(item, 'name', 'title')),
        expires_at=_nested_date(item),
        tariff=_string(tariff),
        price=_as_price(amount, item),
        status=_string(_pick(item, 'status', 'currentStatus', 'state')),
        auto_prolong=autoprolong if isinstance(autoprolong, bool) else None,
        payment_term=_string(_pick(item, 'paymentTerm', 'term')),
    )


def _string(value: object) -> str | None:
    return value[:100] if isinstance(value, str) and value else None


def unwrap(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get('data'), (dict, list)):
        return payload['data']
    return payload


def get_services(payload: object) -> list[dict]:
    payload = unwrap(payload)
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    if isinstance(payload, dict):
        for key in ('items', 'services', 'results'):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
    raise ValueError('Aeza вернула незнакомый формат списка услуг')


class _ProviderRedirectsOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != 'https' or target.hostname not in ('my.aeza.net', 'my.aeza.ru'):
            raise RuntimeError('Aeza API перенаправляет запрос на посторонний сайт')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class AezaClient:
    def __init__(self, api_key: str, base_url: str = 'https://my.aeza.net/api/v2', service_id: str = ''):
        self.api_key = api_key.strip()
        self.service_id = service_id.strip()
        self.base_url = base_url.rstrip('/')
        # Restrict destinations to the provider to avoid leaking the key via config.
        if not re.fullmatch(r'https://my\.aeza\.(?:net|ru)/api(?:/v2)?', self.base_url):
            raise ValueError('AEZA_API_BASE должен быть URL официального API Aeza')
        if self.service_id and not _ALLOWED_IDS.fullmatch(self.service_id):
            raise ValueError('AEZA_SERVICE_ID содержит недопустимые символы')

    def _get(self, path: str) -> object:
        url = f'{self.base_url}/{path.lstrip("/")}'
        req = Request(url, headers={'X-API-Key': self.api_key, 'Accept': 'application/json'}, method='GET')
        try:
            with build_opener(_ProviderRedirectsOnly()).open(req, timeout=12) as response:
                raw = response.read(2_000_001)
        except HTTPError as exc:
            raise RuntimeError(f'Aeza API: HTTP {exc.code} (проверьте API-ключ и URL)') from None
        except (URLError, TimeoutError) as exc:
            raise RuntimeError('Нет соединения с Aeza API') from None
        if len(raw) > 2_000_000:
            raise RuntimeError('Слишком большой ответ Aeza API')
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            raise RuntimeError('Aeza API вернула ответ, который не является JSON') from None
        if isinstance(payload, dict) and payload.get('error'):
            raise RuntimeError('Aeza API вернула ошибку (проверьте права ключа)')
        return payload

    def read_service(self) -> AezaInfo:
        services = get_services(self._get('services'))
        if self.service_id:
            matching = [s for s in services if str(_pick(s, 'id', 'serviceId', 'uuid')) == self.service_id]
            if not matching:
                raise RuntimeError('Услуга с AEZA_SERVICE_ID не найдена в аккаунте')
            selected = matching[0]
        else:
            if len(services) != 1:
                raise RuntimeError(f'У Aeza найдено услуг: {len(services)}. Укажите AEZA_SERVICE_ID в .env')
            selected = services[0]
        sid = str(_pick(selected, 'id', 'serviceId', 'uuid'))
        if not _ALLOWED_IDS.fullmatch(sid):
            raise RuntimeError('Aeza вернула недопустимый ID услуги')
        # Detail endpoint can include payment dates that are absent from list.
        details = unwrap(self._get('services/' + sid))
        if isinstance(details, dict) and isinstance(details.get('service'), dict):
            details = details['service']
        if not isinstance(details, dict):
            raise RuntimeError('Aeza вернула незнакомый формат сведений об услуге')
        combined = {**selected, **details}
        return decode_service(combined)


# Non-idempotent API control; deliberately no retry or redirect.
def _post_restart(client: AezaClient, service_id: str) -> None:
    """POST Aeza API v2 restart once, explicitly non-forced.

    No automatic fallback to legacy API to avoid double reboot.
    HTTP timeout is ambiguous: caller must check provider dashboard manually.
    """
    if not re.fullmatch(r"[0-9]{1,20}", str(service_id)):
        raise ValueError("Нужен числовой ID услуги Aeza")
    if not client.base_url.endswith('/api/v2'):
        raise ValueError("Перезагрузка поддерживается только для Aeza API v2")
    if client.service_id != str(service_id):
        raise ValueError("ID услуги не соответствует AEZA_SERVICE_ID")
    from urllib.request import build_opener
    body = b'{"force":false}'
    req = Request(f'{client.base_url}/services/{service_id}/ctl/restart',
                  headers={'X-API-Key': client.api_key,
                           'Content-Type': 'application/json', 'Accept': 'application/json'},
                  data=body, method='POST')
    # Redirects prohibited for write requests, even to Aeza (no duplicate POSTs).
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError('Aeza перенаправила запрос; действие не повторено')
    try:
        with build_opener(_NoRedirect()).open(req, timeout=12) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError('Aeza не подтвердила запрос')
            raw = response.read(512_001)
    except HTTPError as exc:
        raise RuntimeError(f'Aeza: HTTP {exc.code}; проверьте права API-ключа') from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('Нет ответа от Aeza; проверьте статус в панели перед повтором') from None
    if len(raw) > 512_000:
        raise RuntimeError('Непредвиденный ответ Aeza; проверьте состояние в панели')
    if raw.strip():
        try:
            reply = json.loads(raw)
        except ValueError:
            raise RuntimeError('Непредвиденный ответ Aeza; проверьте состояние в панели') from None
        if isinstance(reply, dict) and (reply.get('error') or reply.get('success') is False):
            raise RuntimeError('Aeza отклонила перезагрузку; проверьте в панели')


AezaClient.restart_once = _post_restart
