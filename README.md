# invitaai-mcp

MCP server for [InvitaAI](https://invitaai.com), a digital invitations platform. Create events, invitations and
personalized guest links, and follow RSVPs from Claude Code, Codex or any MCP client.

> "Create a wedding invitation for Dec 12 at Hacienda Los Arcos, champagne theme, and add my aunt Rosa with 2 seats."

## How it works

```mermaid
sequenceDiagram
    participant U as User
    participant A as AI client + invitaai-mcp (local)
    participant API as invitaai.com API
    A->>API: POST /api/device/code
    API-->>A: user_code + link
    A->>U: "Open the link and confirm BCDF-GHJK"
    U->>API: logs in, clicks Approve
    A->>API: POST /api/device/token (polling)
    API-->>A: token (90 days)
    A->>API: Authorization: Bearer inv_... (events, invitations, guests)
```

The server runs locally over stdio. It holds **no database credentials**: it acts as the user through the same
public API as the web app, so ownership checks and business rules are enforced server-side.

## Security model

| Decision | Why |
|---|---|
| Login with the OAuth 2.0 Device Authorization Grant (RFC 8628) | The user approves in the browser; no password ever reaches the agent. |
| Tokens are random, stored server-side only as SHA-256, expire in 90 days, revocable at `/agentes` | A leaked database doesn't leak usable tokens; access is time-boxed and can be cut. |
| A token can't create or list tokens | A stolen token can't make itself permanent. Renewal always needs a human. |
| Warning from 14 days before expiry | Every tool result carries an `aviso` the agent relays to the user. |
| `device_code` and token never returned to the model | Tool outputs contain links and codes for the user, never secrets. |
| Local token file created with `0600` | Only the user can read it (POSIX). |
| No destructive tools (delete event/guest) in v1 | Limits damage from prompt injection. |
| Guest RSVP messages returned as `mensaje_del_invitado` and flagged in the instructions | Third-party text is data, not instructions. |
| Event type and theme are enums in the tool schema | Invalid values are rejected before reaching the API. |

## Tools

| Tool | Type |
|---|---|
| `conectar_cuenta`, `completar_conexion` | Connect or renew access |
| `estado_conexion` | Read |
| `listar_eventos`, `ver_evento` | Read |
| `crear_evento`, `editar_evento` | Write |
| `crear_invitacion`, `activar_invitacion` | Write |
| `agregar_invitado`, `listar_invitados` | Write / Read |
| `ver_confirmaciones` | Read |

## Install

```bash
git clone https://github.com/David-Solano/invitaai-mcp && cd invitaai-mcp
python -m venv .venv && .venv/bin/pip install -e .     # Windows: .venv\Scripts\pip
claude mcp add invitaai -- /absolute/path/to/.venv/bin/invitaai-mcp
```

Then ask your agent: *"conéctame a InvitaAI"*. `INVITAAI_URL` points it at another deployment (e.g. local dev).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests drive the server through the MCP protocol (in-memory client) against a fake of the InvitaAI API.

Built with Claude Code as a pair programmer; design decisions and review by the author.

MIT
