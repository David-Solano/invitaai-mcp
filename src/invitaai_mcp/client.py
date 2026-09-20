"""HTTP client for the InvitaAI API: device login + authenticated calls.

The MCP never talks to the database. Everything goes through the public API, so the
same auth, ownership checks and business rules apply as in the web app.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import anyio
import httpx

from .credentials import CredentialSource, Credentials, parse_expiry

RENEWAL_WARNING_DAYS = 14


class InvitaAIError(Exception):
    """An error the model can act on (shown to it as the tool error text)."""


class InvitaAIClient:
    def __init__(
        self,
        base_url: str,
        store: CredentialSource,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.store = store
        self.http = http or httpx.AsyncClient(timeout=30)
        self._sleep = sleep
        self._pending: dict | None = None  # device login in progress: device_code, interval, deadline

    # --- Device login (RFC 8628) ------------------------------------------------

    async def start_login(self, client_name: str) -> dict:
        r = await self.http.post(f"{self.base_url}/api/device/code", json={"client_name": client_name})
        r.raise_for_status()
        data = r.json()
        self._pending = {
            "device_code": data["device_code"],  # secret: stays in this process, never returned to the model
            "interval": data.get("interval", 5),
            "deadline": datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"]),
        }
        return {
            "link": data["verification_uri_complete"],
            "codigo": data["user_code"],
            "expira_en_minutos": data["expires_in"] // 60,
        }

    async def finish_login(self, wait_seconds: int = 60) -> bool:
        """Polls until the user approves. True = connected, False = still waiting (call again)."""
        if not self._pending:
            raise InvitaAIError("No hay una conexión en curso. Usa primero la herramienta conectar_cuenta.")
        if datetime.now(timezone.utc) >= self._pending["deadline"]:
            self._pending = None
            raise InvitaAIError("El código expiró. Usa conectar_cuenta para generar uno nuevo.")
        waited = 0
        while True:
            r = await self.http.post(
                f"{self.base_url}/api/device/token", json={"device_code": self._pending["device_code"]}
            )
            if r.status_code == 200:
                data = r.json()
                self.store.save(Credentials(data["access_token"], parse_expiry(data["expires_at"])))
                self._pending = None
                return True

            error = r.json().get("error")
            if error == "slow_down":
                self._pending["interval"] += 5
            elif error != "authorization_pending":
                self._pending = None
                messages = {
                    "access_denied": "El usuario rechazó la conexión en el navegador.",
                    "expired_token": "El código expiró. Usa conectar_cuenta para generar uno nuevo.",
                }
                raise InvitaAIError(messages.get(error, "No se pudo completar la conexión. Intenta de nuevo."))

            if waited + self._pending["interval"] > wait_seconds:
                return False  # the tool returns and the model calls again; no call blocks for long
            await self._sleep(self._pending["interval"])
            waited += self._pending["interval"]

    # --- Authenticated API calls --------------------------------------------------

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        creds = self.store.load()
        if not creds:
            raise InvitaAIError("No estás conectado a InvitaAI. Usa la herramienta conectar_cuenta.")

        r = await self.http.request(
            method, f"{self.base_url}{path}", json=json, headers={"Authorization": f"Bearer {creds.token}"}
        )
        if expires := r.headers.get("X-Token-Expires-At"):
            self.store.update_expiry(parse_expiry(expires))

        if r.status_code == 401:
            detail = _detail(r)
            if isinstance(detail, dict) and detail.get("error") == "token_expired":
                raise InvitaAIError("Tu acceso a InvitaAI venció. Usa conectar_cuenta para renovarlo.")
            # Revoked or invalid: forget it locally so the next step is a clean reconnect.
            self.store.clear()
            raise InvitaAIError("Tu acceso fue revocado o no es válido. Usa conectar_cuenta para conectarte de nuevo.")
        if r.status_code >= 400:
            detail = _detail(r)
            text = detail.get("message") if isinstance(detail, dict) else detail
            raise InvitaAIError(text or f"InvitaAI respondió con error {r.status_code}.")
        return r.json()

    def renewal_warning(self) -> str | None:
        creds = self.store.load()
        if creds and creds.days_left() <= RENEWAL_WARNING_DAYS:
            return (
                f"Tu acceso a InvitaAI vence en {creds.days_left()} días. "
                "Díselo al usuario y ofrécele renovarlo con conectar_cuenta."
            )
        return None

    def link(self, path: str) -> str:
        return f"{self.base_url}{path}"


def _detail(r: httpx.Response) -> Any:
    try:
        return r.json().get("detail")
    except ValueError:
        return None
