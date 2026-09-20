"""Where the API token comes from.

Two modes, same tools:
- local (stdio): the token lives in the user's home, owner-only permissions (CredentialStore).
- remote (HTTP): the token arrives with each request, issued by the OAuth flow (RequestCredentials).

Either way the token is a secret: never printed back to the model, never in the client config.
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


def default_path() -> Path:
    return Path(os.environ.get("INVITAAI_CREDENTIALS", Path.home() / ".invitaai" / "credentials.json"))


@dataclass
class Credentials:
    token: str
    expires_at: datetime  # UTC

    def days_left(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(int((self.expires_at - now).total_seconds() // 86400), 0)


def parse_expiry(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CredentialSource(Protocol):
    """What the client needs to get (and keep) the user's token."""

    def load(self) -> "Credentials | None": ...
    def save(self, creds: "Credentials") -> None: ...
    def update_expiry(self, expires_at: datetime) -> None: ...
    def clear(self) -> None: ...


class RequestCredentials:
    """Remote mode: the token belongs to the HTTP request being served, not to a file.

    Nothing is stored: the MCP client holds the token and sends it on every call.
    """

    def __init__(self, current: Callable[[], Credentials | None]):
        self._current = current

    def load(self) -> "Credentials | None":
        return self._current()

    def save(self, creds: "Credentials") -> None:  # the OAuth flow issues tokens, not the server
        raise NotImplementedError("En modo remoto el token lo emite el flujo de OAuth.")

    def update_expiry(self, expires_at: datetime) -> None:
        pass

    def clear(self) -> None:
        pass


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()

    def load(self) -> Credentials | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Credentials(token=data["token"], expires_at=parse_expiry(data["expires_at"]))
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def save(self, creds: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"token": creds.token, "expires_at": creds.expires_at.isoformat()}
        # Create with 0600 from the start so the secret is never world-readable, even briefly (POSIX).
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def update_expiry(self, expires_at: datetime) -> None:
        creds = self.load()
        if creds and creds.expires_at != expires_at:
            self.save(Credentials(creds.token, expires_at))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
