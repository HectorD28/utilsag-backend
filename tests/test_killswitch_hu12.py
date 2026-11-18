from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import get_db
from app.main import app
from app.models.inventory import AuditLog
from app.schemas.tools import ConsultarStockResponse
from app.services import killswitch_service, tools_service


def _make_fake_db():
    added = []

    def fake_add(obj):
        added.append(obj)

    async def fake_flush():
        for idx, obj in enumerate(added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 500 + idx

    db = MagicMock()
    db.add = MagicMock(side_effect=fake_add)
    db.flush = AsyncMock(side_effect=fake_flush)
    db._added = added
    return db


@pytest.fixture(autouse=True)
def reset_state_and_overrides(monkeypatch):
    killswitch_service.reset_killswitch_for_tests()
    fake_db = _make_fake_db()

    async def _override_get_db() -> AsyncGenerator[MagicMock, None]:
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        "app.api.v1.system.get_settings",
        lambda: Settings(ADMIN_KILLSWITCH_KEY="super-secret-admin-key"),
    )
    yield fake_db
    app.dependency_overrides.clear()
    killswitch_service.reset_killswitch_for_tests()


async def test_killswitch_sin_admin_key_retorna_403():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_missing = await client.post(
            "/api/v1/system/killswitch",
            json={"active": True, "motivo": "Intento sin credencial"},
        )
        resp_wrong = await client.post(
            "/api/v1/system/killswitch",
            headers={"X-Admin-Key": "clave-incorrecta"},
            json={"active": True, "motivo": "Intento con clave mala"},
        )

    assert resp_missing.status_code == 403
    assert resp_missing.json()["error_code"] == "ADMIN_UNAUTHORIZED"
    assert resp_wrong.status_code == 403
    assert resp_wrong.json()["error_code"] == "ADMIN_UNAUTHORIZED"


async def test_killswitch_admin_key_no_configurada_503(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.system.get_settings",
        lambda: Settings(ADMIN_KILLSWITCH_KEY=""),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/system/killswitch",
            headers={"X-Admin-Key": "cualquiera"},
            json={"active": True, "motivo": "Prueba sin key configurada"},
        )

    assert resp.status_code == 503
    assert resp.json()["error_code"] == "KILLSWITCH_KEY_NOT_CONFIGURED"


async def test_killswitch_activar_audita_y_bloquea_agente_y_mutaciones(
    monkeypatch, reset_state_and_overrides
):
    fake_db = reset_state_and_overrides
    monkeypatch.setattr(
        tools_service,
        "consultar_stock",
        AsyncMock(return_value=ConsultarStockResponse(total=0, resumen=[], items=[])),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_toggle = await client.post(
            "/api/v1/system/killswitch",
            headers={"X-Admin-Key": "super-secret-admin-key"},
            json={
                "active": True,
                "motivo": "Comportamiento anómalo detectado en bot",
                "usuario_id": 1,
                "actor_externo": "jefe_soporte",
                "origen": "WEB",
            },
        )
        assert resp_toggle.status_code == 200
        body = resp_toggle.json()
        assert body["active"] is True
        assert body["estado_agente"] == "INACTIVO"
        assert body["audit_log_id"] == 501

        audits = [o for o in fake_db._added if isinstance(o, AuditLog)]
        assert len(audits) == 1
        assert audits[0].accion == "KILLSWITCH_ACTIVADO"
        assert audits[0].usuario_id == 1
        assert audits[0].actor_externo == "jefe_soporte"

        # Agente bloqueado
        resp_agent = await client.post(
            "/api/v1/agent/query", json={"mensaje": "Consulta stock"}
        )
        assert resp_agent.status_code == 503
        assert resp_agent.json()["error_code"] == "KILLSWITCH_ACTIVE"

        # Mutaciones de tools bloqueadas
        resp_mov = await client.post(
            "/api/v1/tools/movimientos",
            json={
                "id_pieza": 1,
                "origen_id": 1,
                "destino_id": 2,
                "motivo": "Traslado bloqueado",
                "tecnico_id": 1,
            },
        )
        assert resp_mov.status_code == 503
        assert resp_mov.json()["error_code"] == "KILLSWITCH_ACTIVE"

        resp_baja = await client.post(
            "/api/v1/tools/bajas",
            json={
                "id_pieza": 1,
                "motivo_falla": "Falla bloqueada",
                "tecnico_id": 1,
            },
        )
        assert resp_baja.status_code == 503
        assert resp_baja.json()["error_code"] == "KILLSWITCH_ACTIVE"

        # Solo lectura sigue operativa
        resp_stock = await client.get("/api/v1/tools/stock")
        assert resp_stock.status_code == 200


async def test_killswitch_desactivar_y_consultar_estado():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/v1/system/killswitch",
            headers={"X-Admin-Key": "super-secret-admin-key"},
            json={"active": True, "motivo": "Detención preventiva"},
        )
        resp_off = await client.post(
            "/api/v1/system/killswitch",
            headers={"X-Admin-Key": "super-secret-admin-key"},
            json={"active": False, "motivo": "Incidente resuelto por jefatura"},
        )
        resp_get = await client.get("/api/v1/system/killswitch")

    assert resp_off.status_code == 200
    assert resp_off.json()["active"] is False
    assert resp_off.json()["estado_agente"] == "ACTIVO"
    assert resp_get.status_code == 200
    assert resp_get.json()["active"] is False


async def test_audit_telegram_denied_registra_audit_log(reset_state_and_overrides):
    fake_db = reset_state_and_overrides
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/system/audit/telegram-denied",
            json={
                "telegram_user_id": 999888,
                "username": "intruso",
                "chat_id": 12345,
                "ip_address": "telegram-polling",
                "detalle": "/start",
            },
        )

    assert resp.status_code == 201
    assert resp.json()["registrado"] is True
    audits = [o for o in fake_db._added if isinstance(o, AuditLog)]
    assert len(audits) == 1
    assert audits[0].accion == "ACCESO_DENEGADO_WHITELIST"
    assert audits[0].origen == "TELEGRAM"
    assert "999888" in (audits[0].actor_externo or "")

