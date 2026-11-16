# Установка / Deployment

> Эта публичная версия содержит **примеры**, а не приватные настройки рабочего VPS. Не запускайте её параллельно с уже работающим ботом с тем же Telegram-токеном.

## Требования

- Linux VPS (проект разрабатывался для Ubuntu 24.04), Python 3.10+, исходящий интернет.
- Собственный Telegram-бот, созданный в BotFather.
- При использовании Aeza API — **собственный** ключ и идентификатор услуги.
- Для запуска с системными метриками — `mode: local` на самом VPS.

## Локальная установка на VPS

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
cd /path/to/Tim-VPS-Checker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
chmod 600 .env config.yaml
```

Измените `.env`:

- `TELEGRAM_BOT_TOKEN` — новый токен вашего бота;
- `TELEGRAM_USER_IDS` — числовые Telegram ID через запятую;
- `TELEGRAM_CONTROL_IDS` — ID из этого же списка, которым разрешены операции `/control`;
- `AEZA_API_KEY` / `AEZA_SERVICE_ID` — ваши данные, при необходимости;
- `AEZA_ALLOW_REBOOT=0` — перезагрузка отключена по умолчанию.

В `config.yaml` настройте локальный сервер и необходимые проверяемые службы. **Не копируйте чужие ID и ключи из примеров.**

```bash
python check.py
python -m unittest discover -s tests -v
python bot.py
```

Проверьте `/start` и `/diag` в личном чате Telegram.

## Постоянная работа через systemd

Отредактируйте [`deploy/vps-monitor.service`](../deploy/vps-monitor.service), заменив `YOUR_LINUX_USER` на отдельного системного пользователя и проверив пути. Пример службы предполагает размещение проекта в `/opt/VPS_Telegram_Monitor`, запуск в режиме `local` и **предварительно созданный** доступный на запись каталог `data/`.

Пример: создайте отдельного пользователя (если он ещё не существует) и подготовьте проект в `/opt/VPS_Telegram_Monitor`. Все команды ниже предполагают, что исходники, `.venv`, `.env` и `config.yaml` уже перенесены в этот каталог. Перенос проекта и создание виртуального окружения выполняются **до** включения ограничений systemd.

```bash
sudo useradd --system --home /opt/VPS_Telegram_Monitor --shell /usr/sbin/nologin vpschecker
# Запускайте один раз; если пользователь уже существует, эту команду пропустите.
sudo chown vpschecker:vpschecker /opt/VPS_Telegram_Monitor/.env /opt/VPS_Telegram_Monitor/config.yaml
sudo chmod 600 /opt/VPS_Telegram_Monitor/.env /opt/VPS_Telegram_Monitor/config.yaml
```

Пользователь службы должен иметь **право чтения исходников и `.venv`**, а также право чтения личных файлов конфигурации. Не предоставляйте ему root только ради мониторинга.

Перед запуском создайте каталог и назначьте владельца (замените `vpschecker` на своё имя системного пользователя):

```bash
sudo mkdir -p /opt/VPS_Telegram_Monitor/data
sudo chown vpschecker:vpschecker /opt/VPS_Telegram_Monitor/data
sudo chmod 700 /opt/VPS_Telegram_Monitor/data
```

В unit включены `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true` и `ReadWritePaths=-/opt/VPS_Telegram_Monitor/data`. Дополнительно выставлены `PYTHONDONTWRITEBYTECODE=1` и `MPLCONFIGDIR=/opt/VPS_Telegram_Monitor/data/.mplconfig`: Python не пытается писать `__pycache__` в каталог исходников, а Matplotlib хранит кэш в разрешённом `data/`. Префикс `-` разрешает запуск службы, даже если путь отсутствует, но **не создаёт каталог**: `data/` всё равно необходимо создать вручную для сохранения SQLite и JSON. Это пример для **локального** мониторинга. Если используете опциональный режим SSH с приватными ключами в домашней папке, `ProtectHome=true` может закрыть к ним доступ: сначала адаптируйте unit и вынесите SSH-ключи в отдельно защищённую директорию. Не выдавайте root без причины.

```bash
sudo cp deploy/vps-monitor.service /etc/systemd/system/vps-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now vps-monitor
systemctl is-active vps-monitor
journalctl -u vps-monitor -n 50 --no-pager
```

**Внимание:** не устанавливайте этот unit поверх действующего бота до резервного копирования и проверки настроек. Публичная копия не содержит установщика для обновления существующего экземпляра на месте.

## Безопасная публикация на GitHub

1. Никогда не добавляйте `.env`, `config.yaml`, `data/`, `*.sqlite3`, ключи SSH, API-токены, приватные логи или резервные копии.
2. Для публичных скриншотов скрывайте IP, токены, ID аккаунтов и иные чувствительные поля.
3. Проверьте `git status` и содержимое коммита до `git push`.
4. Если ключ когда-либо публиковался, **замените его у провайдера**, а не ограничивайтесь удалением файла из Git.


## Автоматические тесты в GitHub Actions

`/.github/workflows/tests.yml` запускает `compileall` и `unittest` на Ubuntu для Python 3.10, 3.12 и 3.13. Никакие реальные ключи, конфигурации сервера или доступ к VPS для этого не нужны.

**Что CI не проверяет:** настоящую Telegram-доставку, фактическую перезагрузку через Aeza API, доступность приватных SSH-ключей при `ProtectHome=true`, поведение systemd на вашей машине и чистую установку на отдельной VM. Перед внедрением unit всё это нужно отдельно проверять в тестовом окружении.
