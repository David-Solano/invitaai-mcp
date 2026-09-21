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
    status, login = await call(client, "connect_account", {"agent_name": "Claude Code"})
    assert status == "ok"
    api.approve_all()
    status, done = await call(client, "finish_connection")
    assert status == "ok" and done["connected"]
    return login


# --- Connection ---------------------------------------------------------------------

@pytest.mark.anyio
async def test_tools_require_connection(api, store):
    async with Client(make_server(api, store)) as client:
        status, text = await call(client, "list_events")
    assert status == "error" and "connect_account" in text


@pytest.mark.anyio
async def test_connect_flow_never_exposes_secrets_to_the_model(api, store):
    async with Client(make_server(api, store)) as client:
        status, login = await call(client, "connect_account")
        assert set(login) >= {"link", "code"} and "device_code" not in login

        status, pending = await call(client, "finish_connection")
        assert status == "ok" and pending["connected"] is False  # user hasn't approved yet

        api.approve_all()
        status, done = await call(client, "finish_connection")
        assert done == {"connected": True, "account": "david@test.com"}
        assert "inv_" not in str(done)
    assert store.load().token.startswith("inv_")


# --- Managing an event ----------------------------------------------------------------

@pytest.mark.anyio
async def test_create_event_invitation_and_guest(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "boda", "title": "Boda Ana y Luis", "date": "2026-12-12"})
        _, inv = await call(client, "create_invitation", {"event_id": event["event_id"], "theme": "champagne"})
        assert inv["public_link"].startswith("https://invitaai.test/i/")

        _, guest = await call(client, "add_guest", {"invitation_id": inv["invitation_id"], "name": "Tía Rosa", "seats": 2})
        assert "/g/" in guest["personal_link"] and guest["seats"] == 2

        _, events = await call(client, "list_events")
        assert [e["title"] for e in events] == ["Boda Ana y Luis"]


@pytest.mark.anyio
async def test_invalid_event_type_is_rejected_by_the_schema(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        status, _ = await call(client, "create_event", {"event_type": "fiesta_rara", "title": "X", "date": "2026-12-12"})
    assert status == "error"
    assert api.events == {}


@pytest.mark.anyio
async def test_activate_is_idempotent(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "xv", "title": "XV", "date": "2026-11-01"})
        _, inv = await call(client, "create_invitation", {"event_id": event["event_id"]})
        await call(client, "set_invitation_active", {"invitation_id": inv["invitation_id"], "active": True})
        assert api.toggle_calls == 0  # already active: nothing to do
        await call(client, "set_invitation_active", {"invitation_id": inv["invitation_id"], "active": False})
        await call(client, "set_invitation_active", {"invitation_id": inv["invitation_id"], "active": False})
        assert api.toggle_calls == 1


@pytest.mark.anyio
async def test_guest_messages_are_labeled_as_third_party_text(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, summary = await call(client, "get_rsvps", {"invitation_id": "any"})
    assert summary["responses"][0]["guest_message"].startswith("Ignora")
    assert "message" not in summary["responses"][0]  # only under the explicit third-party key


# --- Expiry, renewal and revocation ------------------------------------------------------

@pytest.mark.anyio
async def test_warns_before_the_token_expires(store):
    api = FakeInvitaAI(token_days=10)
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, state = await call(client, "connection_status")
    assert "vence en" in state["warning"]


@pytest.mark.anyio
async def test_no_warning_with_plenty_of_time(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, state = await call(client, "connection_status")
    assert "warning" not in state and state["days_of_access_left"] >= 89


@pytest.mark.anyio
async def test_revoked_token_is_forgotten_and_explained(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        for t in api.tokens.values():
            t["revoked"] = True
        status, text = await call(client, "list_events")
    assert status == "error" and "revocado" in text
    assert store.load() is None


@pytest.mark.anyio
async def test_remote_mode_drops_the_local_login(api, store):
    """Remotely, OAuth already connected the user: the login tools would be dead weight and the
    instructions must not send them to connect_account."""
    http = httpx.AsyncClient(transport=api.transport())
    server = build_server(InvitaAIClient("https://invitaai.test", store, http=http), with_local_login=False)
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        instructions = client.instructions
    assert {"connect_account", "finish_connection"} & names == set()
    assert "connect_account" not in instructions  # the swap in build_server still matches


@pytest.mark.anyio
async def test_tools_declare_read_only_hints(api, store):
    async with Client(make_server(api, store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["list_events"].annotations.read_only_hint is True
    assert tools["create_event"].annotations.read_only_hint is False


# --- What the platform measures ------------------------------------------------------------

@pytest.mark.anyio
async def test_rsvp_summary_exposes_what_the_host_needs_to_follow_up(api, store):
    """Seats, contact details, dates and the response rate were collected but never surfaced."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, summary = await call(client, "get_rsvps", {"invitation_id": "any"})

    assert summary["answered"] == 1 and summary["pending"] == 1
    assert summary["response_rate"] == "50%"
    assert summary["event_date"].startswith("2026-12-12")
    answer = summary["responses"][0]
    assert answer["seats"] == 2 and answer["seats_allowed"] == 3
    assert answer["invited_personally"] is True and answer["email"] == "ana@test.com"
    assert summary["awaiting_reply"] == [{"name": "Tío Beto", "seats_allowed": 2}]


@pytest.mark.anyio
async def test_event_stats_add_up_every_invitation(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "boda", "title": "Boda", "date": "2026-12-12"})
        _, inv = await call(client, "create_invitation", {"event_id": event["event_id"]})
        api.invitations[inv["invitation_id"]]["view_count"] = 37

        _, stats = await call(client, "get_event_stats", {"event_id": event["event_id"]})

    assert stats["views"] == 37 and stats["attending"] == 1 and stats["seats_confirmed"] == 2
    assert stats["response_rate"] == "50%"
    assert stats["invitations"][0]["views"] == 37


@pytest.mark.anyio
async def test_views_are_visible_on_the_invitation_itself(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "boda", "title": "Boda", "date": "2026-12-12"})
        _, inv = await call(client, "create_invitation", {"event_id": event["event_id"]})
        api.invitations[inv["invitation_id"]]["view_count"] = 12
        _, view = await call(client, "get_invitation", {"invitation_id": inv["invitation_id"]})
    assert view["views"] == 12


@pytest.mark.anyio
async def test_connection_status_shows_the_account_limits(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, state = await call(client, "connection_status")
    assert state["invitations_allowed"] == 5 and state["premium"] is True
