"""Fase 5, controles del dashboard (`/api/control`): pausar, reanudar y kill switch. Cada defensa
tiene su prueba: login, interruptor, mismo origen, JSON, palabra de confirmacion exacta y rastro de
quien y cuando. Una peticion rechazada NUNCA cambia el estado ni deja fila de auditoria. Misma
semantica y misma advertencia que Telegram. App minima, nunca la real ni ningun puerto."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.api.routes import auth as auth_routes
from app.api.routes import control as control_routes
from app.api.security import COOKIE_NAME, create_session_token, hash_password
from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import control_audit_repo as audit
from app.persistence.repositories import system_state_repo
from app.telegram.commands import CONTROL_WARNINGS
from app.trading import control_state, risk_engine
from app.trading.control_actions import (
    CONFIRM_PHRASES,
    DASHBOARD_WARNINGS,
    STOPS_MEASUREMENT,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correcta-123"
SECRET = "un-secreto-de-prueba-bien-largo-no-el-real"
JSON_HEADERS = {"Content-Type": "application/json"}


def make_settings(**overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "admin", "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
        "JWT_SECRET": SECRET,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_client(
    db: Database, settings: Settings | None = None, *, authenticated: bool = True,
) -> TestClient:
    app = FastAPI()
    app.state.db = db
    app.state.settings = settings or make_settings()
    app.include_router(auth_routes.router)
    app.include_router(control_routes.router)
    client = TestClient(app)
    if authenticated:
        login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 200
    return client


def post(client: TestClient, action: str, confirm: str | None = None, **kwargs):
    body = {} if confirm is None else {"confirm": confirm}
    return client.post(f"/api/control/{action}", json=body, **kwargs)


async def state(db) -> tuple[bool, bool]:
    return (
        await control_state.is_scheduler_paused(db),
        await risk_engine.is_kill_switch_active(db),
    )


# --- login ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/api/control/info"), ("get", "/api/control/audit"),
    ("post", "/api/control/pause"), ("post", "/api/control/resume"),
    ("post", "/api/control/kill"),
])
async def test_every_control_route_requires_a_login(db, method, path):
    client = make_client(db, authenticated=False)
    kwargs = {"json": {"confirm": "PAUSAR"}} if method == "post" else {}

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert await state(db) == (False, False)
    assert await audit.get_recent(db) == []


async def test_a_forged_or_expired_session_changes_nothing(db):
    client = make_client(db, authenticated=False)
    client.cookies.set(COOKIE_NAME, create_session_token("admin", secret="otro-secreto-" * 4))

    assert post(client, "kill", "KILL").status_code == 401
    assert await state(db) == (False, False)


# --- informacion y advertencias ------------------------------------------------------------------


async def test_info_gives_every_action_its_warning_and_confirmation_word(db):
    body = make_client(db).get("/api/control/info").json()

    assert body["enabled"] is True and body["state"] == {"paused": False, "kill_switch": False}
    assert set(body["actions"]) == {"pause", "resume", "kill"}
    for action, entry in body["actions"].items():
        assert entry["confirm_phrase"] == CONFIRM_PHRASES[action]
        assert entry["warning"] == DASHBOARD_WARNINGS[action]
    assert body["actions"]["pause"]["stops_measurement"] is True
    assert body["actions"]["kill"]["stops_measurement"] is True
    assert body["actions"]["resume"]["stops_measurement"] is False


async def test_the_dashboard_warns_exactly_what_telegram_warns(db):
    body = make_client(db).get("/api/control/info").json()["actions"]

    for action, command in (("pause", "/pause"), ("kill", "/kill")):
        assert STOPS_MEASUREMENT in body[action]["warning"]
        assert STOPS_MEASUREMENT in CONTROL_WARNINGS[command]
    assert "DETIENE LA MEDICION" in body["pause"]["warning"]
    assert "ATENCION" in body["pause"]["warning"] and "ATENCION" in body["kill"]["warning"]


async def test_info_reflects_the_current_state(db):
    client = make_client(db)
    post(client, "kill", "KILL")

    assert client.get("/api/control/info").json()["state"] == {
        "paused": True, "kill_switch": True,
    }


# --- ejecucion -----------------------------------------------------------------------------------


async def test_pause_with_the_right_word_pauses_and_leaves_who_and_when(db):
    client = make_client(db)

    response = post(client, "pause", "PAUSAR")
    body = response.json()

    assert response.status_code == 200 and body["ok"] is True and body["changed"] is True
    assert body["before"] == {"paused": False, "kill_switch": False}
    assert body["after"] == {"paused": True, "kill_switch": False}
    assert body["actor"] == "admin" and body["action"] == "pause"
    assert "DETENIDA" in body["message"]
    assert await state(db) == (True, False)
    [row] = await audit.get_recent(db)
    assert (row.channel, row.actor, row.action) == ("DASHBOARD", "admin", "pause")
    assert (row.paused_before, row.paused_after) == (False, True)
    assert row.id == body["audit_id"]


async def test_kill_pauses_too_and_resume_lifts_both(db):
    client = make_client(db)

    kill = post(client, "kill", "KILL").json()
    assert kill["after"] == {"paused": True, "kill_switch": True}
    assert "KILL SWITCH ACTIVO" in kill["message"]
    assert await state(db) == (True, True)

    resume = post(client, "resume", "REANUDAR").json()
    assert resume["after"] == {"paused": False, "kill_switch": False}
    assert await state(db) == (False, False)
    assert [r.action for r in await audit.get_recent(db)] == ["resume", "kill"]


async def test_resume_warns_that_the_drawdown_stop_is_still_active(db):
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")

    body = post(make_client(db), "resume", "REANUDAR").json()

    assert body["drawdown_stop_active"] is True
    assert "stop por drawdown sigue ACTIVO" in body["message"]
    assert await risk_engine.is_drawdown_stop_active(db) is True  # no se toca


async def test_a_repeated_action_is_audited_but_says_nothing_changed(db):
    client = make_client(db)
    post(client, "pause", "PAUSAR")

    again = post(client, "pause", "PAUSAR").json()

    assert again["changed"] is False and again["message"].startswith("Sin cambios")
    assert [r.changed for r in await audit.get_recent(db)] == [False, True]


# --- confirmacion exacta -------------------------------------------------------------------------


@pytest.mark.parametrize("wrong", ["", "pausar", "Pausar", " PAUSAR", "PAUSAR ", "KILL", "SI",
                                   "REANUDAR", "PAUSA"])
async def test_a_wrong_or_missing_word_changes_nothing_and_leaves_no_trace(db, wrong):
    client = make_client(db)

    response = post(client, "pause", wrong)

    assert response.status_code == 422
    assert "PAUSAR" in response.json()["detail"]  # le dice que palabra escribir
    assert await state(db) == (False, False)
    assert await audit.get_recent(db) == []


async def test_a_body_without_the_confirm_field_is_rejected(db):
    client = make_client(db)

    assert post(client, "kill").status_code == 422
    assert client.post("/api/control/kill", json={"confirm": 1}).status_code == 422
    assert client.post("/api/control/kill", json={"confirm": "KILL" * 20}).status_code == 422
    assert await state(db) == (False, False)


async def test_each_action_needs_its_own_word(db):
    client = make_client(db)

    for action, wrong in (("pause", "KILL"), ("kill", "PAUSAR"), ("resume", "KILL")):
        assert post(client, action, wrong).status_code == 422
    assert await state(db) == (False, False)
    assert await audit.get_recent(db) == []


async def test_an_unknown_action_is_rejected_and_get_is_not_allowed(db):
    client = make_client(db)

    assert post(client, "borrar", "BORRAR").status_code == 422
    assert post(client, "info", "PAUSAR").status_code == 422
    assert client.get("/api/control/pause").status_code == 405
    assert client.delete("/api/control/pause").status_code == 405
    assert await state(db) == (False, False)


# --- interruptor ---------------------------------------------------------------------------------


async def test_with_the_controls_switched_off_every_action_is_refused(db):
    client = make_client(db, make_settings(DASHBOARD_CONTROLS_ENABLED=False))

    for action, word in (("pause", "PAUSAR"), ("kill", "KILL"), ("resume", "REANUDAR")):
        response = post(client, action, word)
        assert response.status_code == 403
        assert "DASHBOARD_CONTROLS_ENABLED" in response.json()["detail"]

    assert client.get("/api/control/info").json()["enabled"] is False
    assert await state(db) == (False, False)
    assert await audit.get_recent(db) == []


# --- mismo origen y JSON -------------------------------------------------------------------------


@pytest.mark.parametrize("origin", [
    "http://evil.example", "https://testserver", "http://testserver:9999", "null",
    "http://127.0.0.1:8000",
])
async def test_a_request_from_another_origin_is_refused(db, origin):
    client = make_client(db)

    response = post(client, "kill", "KILL", headers={"Origin": origin})

    assert response.status_code == 403
    assert await state(db) == (False, False)
    assert await audit.get_recent(db) == []


async def test_the_same_origin_is_accepted(db):
    client = make_client(db)

    response = post(client, "pause", "PAUSAR", headers={"Origin": "http://testserver"})

    assert response.status_code == 200 and await state(db) == (True, False)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_a_browser_marking_the_request_as_cross_site_is_refused(db, site):
    client = make_client(db)

    response = post(client, "kill", "KILL", headers={"Sec-Fetch-Site": site})

    assert response.status_code == 403 and await state(db) == (False, False)


@pytest.mark.parametrize("site", ["same-origin", "none"])
async def test_same_origin_fetch_metadata_is_accepted(db, site):
    response = post(make_client(db), "pause", "PAUSAR", headers={"Sec-Fetch-Site": site})

    assert response.status_code == 200


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded",
                                          "multipart/form-data", ""])
async def test_only_json_is_accepted(db, content_type):
    client = make_client(db)
    headers = {"Content-Type": content_type} if content_type else {}

    response = client.post(
        "/api/control/pause", content=json.dumps({"confirm": "PAUSAR"}).encode(), headers=headers,
    )

    assert response.status_code == 415
    assert await state(db) == (False, False)


async def test_json_with_a_charset_is_accepted(db):
    response = make_client(db).post(
        "/api/control/pause", content=b'{"confirm": "PAUSAR"}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 200


# --- la API real ---------------------------------------------------------------------------------


async def test_the_real_app_mounts_the_controls_behind_the_login(db, monkeypatch):
    """Sin entrar al lifespan (no arranca nada): solo el enrutado y el login de `main.app`."""
    monkeypatch.setattr(main.app.state, "db", db, raising=False)
    monkeypatch.setattr(main.app.state, "settings", make_settings(), raising=False)
    client = TestClient(main.app)

    assert client.post("/api/control/kill", json={"confirm": "KILL"}).status_code == 401
    assert client.get("/api/control/info").status_code == 401
    login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert login.status_code == 200
    assert client.post("/api/control/pause", json={"confirm": "PAUSAR"}).status_code == 200
    assert await state(db) == (True, False)


# --- auditoria -----------------------------------------------------------------------------------


async def test_the_audit_endpoint_lists_the_latest_actions_newest_first(db):
    client = make_client(db)
    post(client, "pause", "PAUSAR")
    post(client, "pause", "PAUSAR")
    post(client, "resume", "REANUDAR")

    body = client.get("/api/control/audit").json()

    assert [(e["action"], e["changed"]) for e in body["entries"]] == [
        ("resume", True), ("pause", False), ("pause", True),
    ]
    assert all(e["channel"] == "DASHBOARD" and e["actor"] == "admin" for e in body["entries"])
    assert set(body["entries"][0]) == {
        "id", "created_at", "channel", "actor", "action", "paused_before", "kill_before",
        "paused_after", "kill_after", "changed",
    }
    assert len(client.get("/api/control/audit?limit=1").json()["entries"]) == 1
    assert client.get("/api/control/audit?limit=0").status_code == 422
    assert client.get("/api/control/audit?limit=101").status_code == 422


# --- estructura ----------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def test_the_control_route_cannot_open_or_close_positions_nor_touch_the_measurement():
    forbidden = (
        "anthropic", "app.llm", "app.execution", "app.trading.shadow_live_labeler",
        "app.trading.shadow_book", "app.trading.paper_execution", "app.trading.position_monitor",
        "app.core.scheduler", "app.persistence.repositories.trades_repo",
        "app.persistence.repositories.shadow_repo", "app.news", "app.memory",
    )
    imported = _imports(REPO_ROOT / "app" / "api" / "routes" / "control.py")

    assert [m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)] == []


def test_the_only_write_routes_of_the_api_are_login_logout_and_the_three_controls():
    """Se lee del esquema OpenAPI de la app real: es lo que de verdad queda expuesto."""
    paths = main.app.openapi()["paths"]
    writes = sorted(
        (path, method) for path, methods in paths.items() for method in methods
        if method in {"post", "put", "patch", "delete"}
    )

    assert writes == [
        ("/api/auth/login", "post"), ("/api/auth/logout", "post"),
        ("/api/control/{action}", "post"),
    ]


def test_the_controls_are_declared_with_their_defenses_as_route_dependencies():
    """Las defensas son dependencias de la ruta (se evaluan antes de validar el cuerpo)."""
    route = next(
        r for r in control_routes.router.routes
        if getattr(r, "path", "") == "/api/control/{action}"
    )
    calls = [d.call for d in route.dependant.dependencies]

    for defense in (
        control_routes.require_controls_enabled, control_routes.require_same_origin,
        control_routes.require_json,
    ):
        assert defense in calls
