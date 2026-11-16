"""Durable, private JSON state. Never contains Telegram or provider credentials. The alert detector uses this file to remember active incidents. A corrupt file must never silently reset those incidents (which could duplicate alerts). """
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import tempfile


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise RuntimeError('Файл состояния не должен быть символьной ссылкой')
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if self.path.is_symlink():
                raise RuntimeError('Файл состояния не должен быть символьной ссылкой')
            self.data = {}
            return
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError('Файл состояния повреждён или недоступен; сохраните копию и проверьте его') from exc
        if not isinstance(loaded, dict):
            raise RuntimeError('Файл состояния имеет неверную структуру; автоматический сброс запрещён')
        self.data = loaded

    def get(self, server_id: str) -> dict:
        obj = self.data.get(server_id, {})
        return obj if isinstance(obj, dict) else {}

    def set(self, server_id: str, value: dict) -> None:
        """Atomic durable update: failed write does not change on-disk or memory state."""
        pending = dict(self.data)
        pending[server_id] = value
        encoded = (json.dumps(pending, ensure_ascii=False, indent=2,
                              allow_nan=False) + '\n').encode('utf-8')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError('Файл состояния не должен быть символьной ссылкой')
        fd, name = tempfile.mkstemp(prefix='.state-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            self.data = pending
            # Parent directory metadata must be durable after atomic rename.
            if hasattr(os, 'O_DIRECTORY'):
                dfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)
