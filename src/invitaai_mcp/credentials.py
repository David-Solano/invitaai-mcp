"""Where the API token lives on the user's machine.

The token is a secret: stored in the user's home with owner-only permissions,
never printed back to the model, never written to the MCP client config.
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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
