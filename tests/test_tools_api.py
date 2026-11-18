from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.exceptions import ConcurrencyConflictError, ResourceNotFoundError
from app.main import app
from app.models.enums import EstadoComponente, EstadoEquipo, TipoMovimiento
from app.schemas.tools import (
    ConsultarHistorialEquipoResponse,
    ConsultarStockResponse,
    EquipoDetalle,
    RegistrarBajaComponenteResponse,
    RegistrarMovimientoResponse,
)
from app.services import tools_service


async def _override_get_db() -> AsyncGenerator[None, None]:
    yield None


@pytest.fixture(autouse=True)
def override_db_dependency():
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


async def test_api_consultar_stock_ok(monkeypatch):
    mock_resp = ConsultarStockResponse(total=0, resumen=[], items=[])
    monkeypatch.setattr(
        tools_service, "consultar_stock", AsyncMock(return_value=mock_resp)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/tools/stock?tipo_pieza=RAM")

    assert response.status_code == 200
    assert response.json() == {"total": 0, "resumen": [], "items": []}


async def test_api_consultar_historial_equipo_404(monkeypatch):
    monkeypatch.setattr(
        tools_service,
        "consultar_historial_equipo",
        AsyncMock(side_effect=ResourceNotFoundError("No encontrado")),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/tools/equipos/SBN-404/historial")

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_api_consultar_historial_equipo_ok(monkeypatch):
    mock_resp = ConsultarHistorialEquipoResponse(
        equipo=EquipoDetalle(
            id=1,
            codigo_patrimonial="SBN-100",
            nombre_identificador="PC-01",
            tipo="Desktop",
            estado=EstadoEquipo.OPERATIVO,
            laboratorio_id=1,
            laboratorio_nombre="Lab 1",
            creado_en=datetime.now(timezone.utc),
        ),
        componentes_instalados=[],
        historial_movimientos=[],
    )
    monkeypatch.setattr(
        tools_service, "consultar_historial_equipo", AsyncMock(return_value=mock_resp)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/tools/equipos/SBN-100/historial")

    assert response.status_code == 200
    assert response.json()["equipo"]["codigo_patrimonial"] == "SBN-100"


async def test_api_registrar_movimiento_conflict_409(monkeypatch):
    monkeypatch.setattr(
        tools_service,
        "registrar_movimiento",
        AsyncMock(side_effect=ConcurrencyConflictError("Conflicto")),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/tools/movimientos",
            json={
                "id_pieza": 1,
                "origen_id": 1,
                "destino_id": 2,
                "motivo": "Traslado de prueba",
                "tecnico_id": 1,
            },
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "CONCURRENCY_CONFLICT"


async def test_api_registrar_movimiento_ok(monkeypatch):
    mock_resp = RegistrarMovimientoResponse(
        movimiento_id=50,
        componente_id=1,
        tipo_movimiento=TipoMovimiento.TRASLADO,
        estado_componente=EstadoComponente.EN_STOCK,
        lab_origen_id=1,
        lab_destino_id=2,
        audit_log_id=99,
    )
    monkeypatch.setattr(
        tools_service, "registrar_movimiento", AsyncMock(return_value=mock_resp)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/tools/movimientos",
            json={
                "id_pieza": 1,
                "origen_id": 1,
                "destino_id": 2,
                "motivo": "Traslado de prueba",
                "tecnico_id": 1,
            },
        )

    assert response.status_code == 201
    assert response.json()["movimiento_id"] == 50


async def test_api_registrar_baja_ok(monkeypatch):
    mock_resp = RegistrarBajaComponenteResponse(
        baja_id=12,
        componente_id=1,
        estado_componente=EstadoComponente.DEFECTUOSO,
        estado_patrimonial="PENDIENTE_USGOM",
        audit_log_id=100,
        mensaje="Propuesta registrada",
    )
    monkeypatch.setattr(
        tools_service, "registrar_baja_componente", AsyncMock(return_value=mock_resp)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/tools/bajas",
            json={
                "id_pieza": 1,
                "motivo_falla": "Falla en módulo de memoria",
                "tecnico_id": 1,
            },
        )

    assert response.status_code == 201
    assert response.json()["estado_patrimonial"] == "PENDIENTE_USGOM"

