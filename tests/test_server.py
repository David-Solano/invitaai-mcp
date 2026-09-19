"""End-to-end through the MCP protocol (in-memory client) against a fake InvitaAI API."""
import json

import httpx
import pytest
from mcp import Client, MCPError

from invitaai_mcp.client import InvitaAIClient
from invitaai_mcp.credentials import CredentialStore
from invitaai_mcp.server import build_server

from fake_api import FakeInvitaAI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api():
    return FakeInvitaAI()


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "credentials.json")


async def no_sleep(_seconds):
    pass


def make_server(api, store):
    http = httpx.AsyncClient(transport=api.transport())
    return build_server(InvitaAIClient("https://invitaai.test", store, http=http, sleep=no_sleep))


async def call(client, name, args=None):
    """Returns ("ok", data) or ("error", text), whichever way the SDK reports tool errors."""
    try:
        r = await client.call_tool(name, args or {})
    except MCPError as e:
        return "error", e.message
    if r.is_error:
        return "error", r.content[0].text
    if r.structured_content is not None:
        data = r.structured_content
        return "ok", data["result"] if set(data) == {"result"} else data
    return "ok", json.loads(r.content[0].text) if len(r.content) == 1 else [json.loads(c.text) for c in r.content]


async def connect(client, api):
    status, login = await call(client, "conectar_cuenta", {"nombre_del_agente": "Claude Code"})
    assert status == "ok"
    api.approve_all()
    status, done = await call(client, "completar_conexion")
    assert status == "ok" and done["conectado"]
    return login


# --- Connection ---------------------------------------------------------------------

@pytest.mark.anyio
async def test_tools_require_connection(api, store):
    async with Client(make_server(api, store)) as client:
        status, text = await call(client, "listar_eventos")
    assert status == "error" and "conectar_cuenta" in text


@pytest.mark.anyio
async def test_connect_flow_never_exposes_secrets_to_the_model(api, store):
    async with Client(make_server(api, store)) as client:
        status, login = await call(client, "conectar_cuenta")
        assert set(login) >= {"link", "codigo"} and "device_code" not in login

        status, pending = await call(client, "completar_conexion")
        assert status == "ok" and pending["conectado"] is False  # user hasn't approved yet

        api.approve_all()
        status, done = await call(client, "completar_conexion")
        assert done == {"conectado": True, "cuenta": "david@test.com"}
        assert "inv_" not in str(done)
    assert store.load().token.startswith("inv_")


# --- Managing an event ----------------------------------------------------------------

@pytest.mark.anyio
async def test_create_event_invitation_and_guest(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "crear_evento", {"tipo": "boda", "titulo": "Boda Ana y Luis", "fecha": "2026-12-12"})
        _, inv = await call(client, "crear_invitacion", {"evento_id": event["evento_id"], "tema": "champagne"})
        assert inv["link_publico"].startswith("https://invitaai.test/i/")

        _, guest = await call(client, "agregar_invitado", {"invitacion_id": inv["invitacion_id"], "nombre": "Tía Rosa", "lugares": 2})
        assert "/g/" in guest["link_personal"] and guest["lugares"] == 2

        _, events = await call(client, "listar_eventos")
        assert [e["titulo"] for e in events] == ["Boda Ana y Luis"]


@pytest.mark.anyio
async def test_invalid_event_type_is_rejected_by_the_schema(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        status, _ = await call(client, "crear_evento", {"tipo": "fiesta_rara", "titulo": "X", "fecha": "2026-12-12"})
    assert status == "error"
    assert api.events == {}


@pytest.mark.anyio
async def test_activate_is_idempotent(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "crear_evento", {"tipo": "xv", "titulo": "XV", "fecha": "2026-11-01"})
        _, inv = await call(client, "crear_invitacion", {"evento_id": event["evento_id"]})
        await call(client, "activar_invitacion", {"invitacion_id": inv["invitacion_id"], "activa": True})
        assert api.toggle_calls == 0  # already active: nothing to do
        await call(client, "activar_invitacion", {"invitacion_id": inv["invitacion_id"], "activa": False})
        await call(client, "activar_invitacion", {"invitacion_id": inv["invitacion_id"], "activa": False})
        assert api.toggle_calls == 1


@pytest.mark.anyio
async def test_guest_messages_are_labeled_as_third_party_text(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, summary = await call(client, "ver_confirmaciones", {"invitacion_id": "any"})
    assert summary["respuestas"][0]["mensaje_del_invitado"].startswith("Ignora")
    assert "mensaje" not in summary["respuestas"][0]  # only under the explicit third-party key


# --- Expiry, renewal and revocation ------------------------------------------------------

@pytest.mark.anyio
async def test_warns_before_the_token_expires(store):
    api = FakeInvitaAI(token_days=10)
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, state = await call(client, "estado_conexion")
    assert "vence en" in state["aviso"]


@pytest.mark.anyio
async def test_no_warning_with_plenty_of_time(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, state = await call(client, "estado_conexion")
    assert "aviso" not in state and state["dias_de_acceso_restantes"] >= 89


@pytest.mark.anyio
async def test_revoked_token_is_forgotten_and_explained(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        for t in api.tokens.values():
            t["revoked"] = True
        status, text = await call(client, "listar_eventos")
    assert status == "error" and "revocado" in text
    assert store.load() is None


@pytest.mark.anyio
async def test_tools_declare_read_only_hints(api, store):
    async with Client(make_server(api, store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["listar_eventos"].annotations.read_only_hint is True
    assert tools["crear_evento"].annotations.read_only_hint is False
