"""Editing an existing invitation: the gaps found in the first real test run.

The agent had no way to change an invitation, so it created a second one; and photos
were invisible to it because no tool exposed them.
"""
import pytest
from mcp import Client

from test_server import api, anyio_backend, call, connect, make_server, store  # noqa: F401


async def setup_invitation(client, api, **kwargs):
    _, event = await call(client, "create_event", {"event_type": "boda", "title": "Boda", "date": "2026-12-12"})
    _, inv = await call(client, "create_invitation", {"event_id": event["event_id"], **kwargs})
    return event, inv


@pytest.mark.anyio
async def test_creating_a_second_invitation_is_refused_with_a_pointer_to_editing(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        event, _ = await setup_invitation(client, api)
        status, text = await call(client, "create_invitation", {"event_id": event["event_id"]})
    assert status == "error" and "update_invitation" in text
    assert len(api.invitations) == 1


@pytest.mark.anyio
async def test_texts_written_by_the_model_are_saved_on_creation(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api, headline="Ana & Luis", message="Nos casamos")
        _, view = await call(client, "get_invitation", {"invitation_id": inv["invitation_id"]})
    assert view["texts"]["headline"] == "Ana & Luis"
    assert view["texts"]["message"] == "Nos casamos"
    # the sections the model didn't write keep the platform's template text
    assert view["texts"]["dress_code"] == "Casual elegante"


@pytest.mark.anyio
async def test_editing_one_text_keeps_the_others_and_the_same_link(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api, headline="Ana & Luis", message="Nos casamos")
        _, edited = await call(client, "update_invitation", {
            "invitation_id": inv["invitation_id"], "message": "Nos casamos, acompáñanos", "theme": "champagne",
        })
        _, view = await call(client, "get_invitation", {"invitation_id": inv["invitation_id"]})

    assert edited["public_link"] == inv["public_link"]  # same invitation, same shared link
    assert view["texts"]["message"] == "Nos casamos, acompáñanos"
    assert view["texts"]["headline"] == "Ana & Luis"  # untouched
    assert view["theme"] == "champagne"
    assert len(api.invitations) == 1


@pytest.mark.anyio
async def test_editing_nothing_is_an_error(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "update_invitation", {"invitation_id": inv["invitation_id"]})
    assert status == "error" and "al menos" in text


# --- Photos and music ---------------------------------------------------------------------

@pytest.mark.anyio
async def test_photo_catalog_is_offered_by_name(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, photos = await call(client, "search_photos", {"tag": "boda"})
    assert photos["photos"][0]["name"] == "Rosa y dorado"


@pytest.mark.anyio
async def test_cover_gallery_and_music_accumulate_instead_of_replacing(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        await call(client, "set_cover_photo", {"invitation_id": iid, "photo_url": "https://cdn.test/rosa.png"})
        await call(client, "add_gallery_photos", {"invitation_id": iid, "urls": ["https://cdn.test/a.jpg"]})
        await call(client, "add_gallery_photos", {"invitation_id": iid,
                                                     "urls": ["https://cdn.test/a.jpg", "https://cdn.test/b.webp"]})
        await call(client, "set_music", {"invitation_id": iid,
                                            "song_link": "https://youtu.be/abc12345678",
                                            "title": "Perfect"})
        _, view = await call(client, "get_invitation", {"invitation_id": iid})

    assert view["cover_photo"] == "https://cdn.test/rosa.png"  # survived the later design edits
    assert view["gallery_photos"] == ["https://cdn.test/a.jpg", "https://cdn.test/b.webp"]  # no duplicates
    assert view["music"] == "Perfect"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://cdn.test/a.png", "https://cdn.test/malware.exe"])
@pytest.mark.anyio
async def test_only_public_image_links_are_accepted(api, store, url):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "set_cover_photo", {"invitation_id": inv["invitation_id"], "photo_url": url})
    assert status == "error" and "no es una imagen pública válida" in text
    assert "create_photo_upload_link" in text  # dead end -> next step


# --- Guided flow ------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_guided_prompt_interviews_the_user(api, store):
    async with Client(make_server(api, store)) as client:
        result = await client.get_prompt("guided_invitation", {"event_type": "xv"})
    script = result.messages[0].content.text
    assert "xv" in script and "una pregunta a la vez" in script.lower()
    assert "update_invitation" in script and "search_photos" in script


@pytest.mark.anyio
async def test_upload_link_lets_the_user_send_their_own_photos(api, store):
    """Agents can't receive files, so the tool hands the user a link instead."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, ticket = await call(client, "create_photo_upload_link", {
            "invitation_id": inv["invitation_id"], "target": "cover",
        })
    assert status == "ok"
    assert ticket["link"].startswith("https://invitaai.test/subir/")
    assert ticket["target"] == "cover" and ticket["expires_in_minutes"] == 30


# --- Design freedom ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_agent_can_read_the_design_catalog(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, catalog = await call(client, "get_design_options")
    assert catalog["textures"][0]["label"] == "Lino"
    assert "title_fonts" in catalog and "cover_styles" in catalog


@pytest.mark.anyio
async def test_design_changes_merge_and_keep_photos(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        await call(client, "set_cover_photo", {"invitation_id": iid, "photo_url": "https://cdn.test/rosa.png"})
        _, applied = await call(client, "customize_design", {
            "invitation_id": iid, "texture": "lino", "ornament": "floral",
            "title_font": "'Great Vibes', cursive", "primary_color": "#7A1535",
        })
        _, view = await call(client, "get_invitation", {"invitation_id": iid})

    design = api.invitations[inv["invitation_id"]]["design"]
    assert design["texture"] == "lino" and design["decoration"] == "floral"
    assert design["custom_colors"] == {"primary": "#7A1535"}
    assert view["cover_photo"] == "https://cdn.test/rosa.png"  # design edits don't wipe the photo
    assert applied["public_link"] == inv["public_link"]


@pytest.mark.anyio
async def test_a_second_color_is_added_without_losing_the_first(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        await call(client, "customize_design", {"invitation_id": iid, "primary_color": "#7A1535"})
        await call(client, "customize_design", {"invitation_id": iid, "text_color": "#250812"})
    assert api.invitations[iid]["design"]["custom_colors"] == {"primary": "#7A1535", "text": "#250812"}


@pytest.mark.parametrize("args,esperado", [
    ({"texture": "terciopelo"}, "no es válido para textures"),
    ({"primary_color": "vino tinto"}, "color hex válido"),
    ({}, "al menos un elemento"),
])
@pytest.mark.anyio
async def test_invalid_design_values_are_rejected_with_the_options(api, store, args, esperado):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "customize_design", {"invitation_id": inv["invitation_id"], **args})
    assert status == "error" and esperado in text


# --- Music ------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_invented_song_links_are_caught(api, store):
    """Models make up plausible links; the API checks the track exists before it's saved."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "set_music", {
            "invitation_id": inv["invitation_id"],
            "song_link": "https://open.spotify.com/track/inventado123",
        })
    assert status == "error" and "copie el link" in text
    assert "music_embed_url" not in api.invitations[inv["invitation_id"]]["design"]


@pytest.mark.anyio
async def test_real_link_saves_the_real_title(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        _, music = await call(client, "set_music", {
            "invitation_id": inv["invitation_id"], "song_link": "https://youtu.be/abc12345678",
        })
    assert music["music"] == "Perfect - Ed Sheeran" and music["provider"] == "youtube"
    assert "nota" not in music


@pytest.mark.anyio
async def test_spotify_warns_about_the_30_second_preview(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        _, music = await call(client, "set_music", {
            "invitation_id": inv["invitation_id"], "song_link": "https://open.spotify.com/track/real",
        })
    assert "30 segundos" in music["note"]


# --- Location ----------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_an_address_that_cannot_be_located_is_reported_back(api, store):
    """The map button only appears when the address resolves, so the agent must know."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {
            "event_type": "cumpleanos", "title": "Cumple", "date": "2026-11-19",
            "location": "Calle inventada 99999, Ciudad Falsa",
        })
    assert "No se pudo ubicar" in event["location_note"] and "Google Maps" in event["location_note"]


@pytest.mark.anyio
async def test_a_good_address_confirms_the_map(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {
            "event_type": "boda", "title": "Boda", "date": "2026-12-12", "location": "Parque España, Condesa, CDMX",
        })
        _, edited = await call(client, "update_event", {
            "event_id": event["event_id"], "location": "Parque México, Condesa, CDMX",
        })
    assert "se ubicó" in event["location_note"] and "se ubicó" in edited["location_note"]
    assert edited["updated"] == ["location"]


@pytest.mark.anyio
async def test_no_location_no_noise(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "boda", "title": "Boda", "date": "2026-12-12"})
    assert "ubicacion" not in event


@pytest.mark.anyio
async def test_unknown_theme_is_rejected_with_the_real_list(api, store):
    """Themes are no longer copied into the MCP: they are checked against the platform catalog."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, event = await call(client, "create_event", {"event_type": "boda", "title": "B", "date": "2026-12-12"})
        status, text = await call(client, "create_invitation", {
            "event_id": event["event_id"], "theme": "tema_inventado",
        })
    assert status == "error" and "'champagne'" in text and "no es válido para themes" in text


@pytest.mark.anyio
async def test_unknown_event_type_is_rejected(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        status, text = await call(client, "create_event", {
            "event_type": "posada", "title": "Posada", "date": "2026-12-20",
        })
    assert status == "error" and "event_types" in text
    assert api.events == {}


@pytest.mark.anyio
async def test_the_opening_animation_can_be_chosen(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        _, antes = await call(client, "get_invitation", {"invitation_id": iid})

        await call(client, "customize_design", {"invitation_id": iid, "opening": "confeti"})
        _, con_confeti = await call(client, "get_invitation", {"invitation_id": iid})

        status, text = await call(client, "customize_design", {"invitation_id": iid, "opening": "fuegos"})

    assert antes["opening"] == "sobre"  # the envelope is still the default
    assert con_confeti["opening"] == "confeti"
    assert status == "error" and "'petalos'" in text


@pytest.mark.anyio
async def test_the_envelope_animation_can_be_turned_off_and_on(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        _, antes = await call(client, "get_invitation", {"invitation_id": iid})

        await call(client, "customize_design", {"invitation_id": iid, "opening": "directo"})
        _, sin_sobre = await call(client, "get_invitation", {"invitation_id": iid})

        await call(client, "customize_design", {"invitation_id": iid, "opening": "sobre",
                                                   "texture": "lino"})
        _, con_sobre = await call(client, "get_invitation", {"invitation_id": iid})

    assert antes["opening"] == "sobre"  # invitations open with the envelope by default
    assert sin_sobre["opening"] == "directo"
    assert con_sobre["opening"] == "sobre"
    assert api.invitations[iid]["design"]["texture"] == "lino"  # other design values survive


# --- Cover readability --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cover_text_can_be_made_readable_over_the_photo(api, store):
    """"La letra se pierde con el fondo": colour, dark veil and position, without moving the text out."""
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        await call(client, "customize_design", {
            "invitation_id": iid, "cover_text_color": "#ffffff",
            "darken_photo": True, "cover_text_height": 35,
        })
        _, view = await call(client, "get_invitation", {"invitation_id": iid})

    design = api.invitations[iid]["design"]
    assert design["hero_text_color"] == "#ffffff" and design["hero_overlay"] is True
    assert design["hero_text_y"] == 35
    assert view["cover"]["text_color"] == "#ffffff"


@pytest.mark.anyio
async def test_cover_text_colour_can_go_back_to_the_theme(api, store):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        iid = inv["invitation_id"]
        await call(client, "customize_design", {"invitation_id": iid, "cover_text_color": "#ffffff"})
        await call(client, "customize_design", {"invitation_id": iid, "cover_text_color": "auto"})
        _, view = await call(client, "get_invitation", {"invitation_id": iid})
    assert view["cover"]["text_color"] == "auto"


@pytest.mark.parametrize("args,esperado", [
    ({"cover_text_color": "blanco"}, "hex"),
    ({"cover_text_height": 140}, "0 a 100"),
])
@pytest.mark.anyio
async def test_bad_cover_values_are_rejected(api, store, args, esperado):
    async with Client(make_server(api, store)) as client:
        await connect(client, api)
        _, inv = await setup_invitation(client, api)
        status, text = await call(client, "customize_design", {"invitation_id": inv["invitation_id"], **args})
    assert status == "error" and esperado in text
