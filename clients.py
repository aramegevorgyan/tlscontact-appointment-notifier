"""Encrypted, owner-only client records. One process and one selected client."""
import copy
import fcntl
import json
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class ClientStore:
    def __init__(self, path: Path, key_path: Path):
        self.path, self.key_path = Path(path), Path(key_path)
        if self.path.resolve() == self.key_path.resolve():
            raise ValueError('Client data and encryption key must use different files')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = self.path.with_suffix('.lock').open('a')
        os.chmod(self._lock.name, 0o600)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.key_path.exists():
                if self.path.exists():
                    raise ValueError('Client encryption key is missing; restore it from backup')
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
            os.chmod(self.key_path, 0o600)
            self._cipher = Fernet(self.key_path.read_bytes().strip())
            self._data = {'version': 1, 'selected': None, 'clients': {}}
            if self.path.exists():
                os.chmod(self.path, 0o600)
                self._data = json.loads(self._cipher.decrypt(self.path.read_bytes()))
                self._validate()
            self.revision = 0
        except (InvalidToken, json.JSONDecodeError, KeyError, TypeError):
            self.close()
            raise ValueError('Client data cannot be read; check the encryption key and backup') from None
        except Exception:
            self.close()
            raise

    def _validate(self):
        if (self._data.get('version') != 1 or not isinstance(self._data.get('clients'), dict)
                or len(self._data['clients']) > 100):
            raise ValueError('Invalid client data format')
        if self._data['selected'] is not None and self._data['selected'] not in self._data['clients']:
            raise ValueError('Invalid selected client')
        for key, client in self._data['clients'].items():
            if not isinstance(key, str) or not isinstance(client, dict):
                raise ValueError('Invalid client record')
            for field in ('label', 'email', 'password', 'target_date', 'appointment_url'):
                if not isinstance(client.get(field), str) or not client[field]:
                    raise ValueError('Invalid client record')
            date.fromisoformat(client['target_date'])
            if not isinstance(client.get('sent'), list) or not all(isinstance(x, str) for x in client['sent']):
                raise ValueError('Invalid client alert history')

    def _save(self, updated):
        ciphertext = self._cipher.encrypt(json.dumps(updated).encode())
        fd, temporary = tempfile.mkstemp(prefix='.clients-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._data = updated
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def add(self, label, email, password, target_date, appointment_url):
        if len(self._data['clients']) >= 100:
            raise ValueError('Client limit reached; remove completed clients first')
        # Same email may legitimately have several applications/calendars.
        updated = copy.deepcopy(self._data)
        client_id = uuid.uuid4().hex[:12]
        updated['clients'][client_id] = dict(label=label, email=email, password=password,
                                           target_date=target_date, appointment_url=appointment_url, sent=[])
        self._save(updated)
        return client_id

    def get(self, client_id):
        client = self._data['clients'].get(client_id)
        return copy.deepcopy(client) if client is not None else None

    @property
    def selected(self):
        return self._data['selected']

    def summaries(self):
        return [(key, value['label'], value['target_date']) for key, value in self._data['clients'].items()]

    def select(self, client_id):
        if client_id is not None and client_id not in self._data['clients']:
            raise ValueError('Client not found. Send /clients to see saved clients.')
        updated = copy.deepcopy(self._data)
        updated['selected'] = client_id
        self._save(updated)
        self.revision += 1

    def delete(self, client_id):
        if client_id not in self._data['clients']:
            raise ValueError('Client not found. Send /clients to see saved clients.')
        updated = copy.deepcopy(self._data)
        del updated['clients'][client_id]
        if updated['selected'] == client_id:
            updated['selected'] = None
        self._save(updated)
        self.revision += 1

    def record(self, client_id, day):
        if client_id not in self._data['clients']:
            return
        updated = copy.deepcopy(self._data)
        sent = updated['clients'][client_id]['sent']
        if day not in sent:
            sent.append(day)
            self._save(updated)

    def close(self):
        if getattr(self, '_lock', None) is not None:
            self._lock.close()
