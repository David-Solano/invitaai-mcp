"""Editing an existing invitation: the gaps found in the first real test run.

The agent had no way to change an invitation, so it created a second one; and photos
were invisible to it because no tool exposed them.
"""
import pytest
from mcp import Client

from test_server import api, anyio_backend, call, connect, make_server, store  # noqa: F401


async def setup_invitation(client, api, **kwargs):
    _, event = await call(client, "crear_evento", {"tipo": "boda", "titulo": "Boda", "fecha": "2026-12-12"})
    _, inv = await call(client, "crear_invitacion", {"evento_id": event["evento_id"], **kwargs})
    return event, inv


@pytest.mark.anyio
async def test_creating_a_second_invitation_is_refused_with_a_pointer_to_editing(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        event, _ = await setup_invitation(client, api)
        status, text = await call(client, "crear_invitacion", {"evento_id": event["evento_id"]})
    assert status == "error" and "editar_invitacion" in text
    assert len(api.invitations) == 1


@pytest.mark.anyio
async def test_texts_written_by_the_model_are_saved_on_creation(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api, titulo_principal="Ana & Luis", mensaje="Nos casamos")
        _, view = await call(client, "ver_invitacion", {"invitacion_id": inv["invitacion_id"]})
    assert view["textos"]["titulo_principal"] == "Ana & Luis"
    assert view["textos"]["mensaje"] == "Nos casamos"
    # the sections the model didn't write keep the platform's template text
    assert view["textos"]["codigo_vestimenta"] == "Casual elegante"


@pytest.mark.anyio
async def test_editing_one_text_keeps_the_others_and_the_same_link(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api, titulo_principal="Ana & Luis", mensaje="Nos casamos")
        _, edited = await call(client, "editar_invitacion", {
            "invitacion_id": inv["invitacion_id"], "mensaje": "Nos casamos, acompáñanos", "tema": "champagne",
        })
        _, view = await call(client, "ver_invitacion", {"invitacion_id": inv["invitacion_id"]})

    assert edited["link_publico"] == inv["link_publico"]  # same invitation, same shared link
    assert view["textos"]["mensaje"] == "Nos casamos, acompáñanos"
    assert view["textos"]["titulo_principal"] == "Ana & Luis"  # untouched
    assert view["tema"] == "champagne"
    assert len(api.invitations) == 1


@pytest.mark.anyio
async def test_editing_nothing_is_an_error(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "editar_invitacion", {"invitacion_id": inv["invitacion_id"]})
    assert status == "error" and "al menos" in text


# --- Photos and music ---------------------------------------------------------------------

@pytest.mark.anyio
async def test_photo_catalog_is_offered_by_name(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, photos = await call(client, "buscar_fotos", {"etiqueta": "boda"})
    assert photos["fotos"][0]["nombre"] == "Rosa y dorado"


@pytest.mark.anyio
async def test_cover_gallery_and_music_accumulate_instead_of_replacing(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitacion_id"]
        await call(client, "cambiar_foto_portada", {"invitacion_id": iid, "url_foto": "https://cdn.test/rosa.png"})
        await call(client, "agregar_fotos_galeria", {"invitacion_id": iid, "urls": ["https://cdn.test/a.jpg"]})
        await call(client, "agregar_fotos_galeria", {"invitacion_id": iid,
                                                     "urls": ["https://cdn.test/a.jpg", "https://cdn.test/b.webp"]})
        await call(client, "poner_musica", {"invitacion_id": iid, "url_embed": "https://open.spotify.com/embed/x",
                                            "titulo": "Perfect"})
        _, view = await call(client, "ver_invitacion", {"invitacion_id": iid})

    assert view["foto_portada"] == "https://cdn.test/rosa.png"  # survived the later design edits
    assert view["fotos_galeria"] == ["https://cdn.test/a.jpg", "https://cdn.test/b.webp"]  # no duplicates
    assert view["musica"] == "Perfect"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://cdn.test/a.png", "https://cdn.test/malware.exe"])
@pytest.mark.anyio
async def test_only_public_image_links_are_accepted(api, store, url):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "cambiar_foto_portada", {"invitacion_id": inv["invitacion_id"], "url_foto": url})
    assert status == "error" and "no es una imagen pública válida" in text
    assert "crear_link_para_subir_fotos" in text  # dead end -> next step


# --- Guided flow ------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_guided_prompt_interviews_the_user(api, store):
    async with Client(make_server(api, store)) as client:
        result = await client.get_prompt("crear_invitacion_guiada", {"tipo_de_evento": "xv"})
    script = result.messages[0].content.text
    assert "xv" in script and "una pregunta a la vez" in script.lower()
    assert "editar_invitacion" in script and "buscar_fotos" in script


@pytest.mark.anyio
async def test_upload_link_lets_the_user_send_their_own_photos(api, store):
    """Agents can't receive files, so the tool hands the user a link instead."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, ticket = await call(client, "crear_link_para_subir_fotos", {
            "invitacion_id": inv["invitacion_id"], "destino": "portada",
        })
    assert status == "ok"
    assert ticket["link"].startswith("https://invitaai.test/subir/")
    assert ticket["destino"] == "foto principal" and ticket["expira_en_minutos"] == 30
