from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import get_db
from app.core.exceptions import UtilSAGError
from app.main import app
from app.models.enums import EstadoComponente, TipoMovimiento
from app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from app.schemas.tools import ConsultarStockResponse, RegistrarMovimientoResponse
from app.services import agent_service, tools_service
from app.services.llm_client import HttpxLLMClient, LLMCallResult
from app.services.privacy_service import mask_pii


class _FakeLLMClient:
    def __init__(self, responses: list[str], model: str = "fake:test-model"):
        self._responses = list(responses)
        self._model = model
        self.calls: list[dict] = []

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(self, *, system_prompt, messages, json_mode=False):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": list(messages),
                "json_mode": json_mode,
            }
        )
        return LLMCallResult(content=self._responses.pop(0), model=self._model)


async def _override_get_db() -> AsyncGenerator[None, None]:
    yield None


@pytest.fixture(autouse=True)
def override_db_dependency():
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


def test_mask_pii_oculta_datos_personales_y_preserva_sbn():
    texto = (
        "Revisar equipo SBN 740868655163. Contacto: juan.perez@unmsm.edu.pe, "
        "cel +51 987 654 321, DNI: 45678912."
    )
    res = mask_pii(texto)

    assert "740868655163" in res.masked_text
    assert "juan.perez@unmsm.edu.pe" not in res.masked_text
    assert "987 654 321" not in res.masked_text
    assert "45678912" not in res.masked_text
    assert "[EMAIL_REDACTED]" in res.masked_text
    assert "[PHONE_REDACTED]" in res.masked_text
    assert "[DNI_REDACTED]" in res.masked_text
    assert res.redactions == ["EMAIL:1", "PHONE:1", "DNI:1"]


async def test_agent_consultar_stock_con_reintento_json(monkeypatch):
    mock_stock = ConsultarStockResponse(total=2, resumen=[], items=[])
    monkeypatch.setattr(
        tools_service, "consultar_stock", AsyncMock(return_value=mock_stock)
    )

    llm = _FakeLLMClient(
        [
            "{json_malformado",
            '{"tool_name": "consultar_stock", "arguments": {"tipo_pieza": "RAM"}}',
            "Hay 2 memorias RAM disponibles según el inventario.",
        ]
    )

    req = AgentQueryRequest(mensaje="¿Cuánta RAM hay? Mi correo es test@unmsm.edu.pe")
    resp = await agent_service.procesar_consulta_agente(
        None, req, llm_client=llm  # type: ignore[arg-type]
    )

    assert resp.reintentos_json == 1
    assert resp.redacciones_aplicadas == ["EMAIL:1"]
    assert "test@unmsm.edu.pe" not in llm.calls[0]["messages"][0]["content"]
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "consultar_stock"
    assert resp.tool_calls[0].executed is True
    assert "2 memorias RAM" in resp.respuesta


async def test_agent_falla_tras_agotar_reintentos_json():
    llm = _FakeLLMClient(["no-json-1", "no-json-2", "no-json-3"])
    req = AgentQueryRequest(mensaje="Consulta cualquiera")

    with pytest.raises(UtilSAGError) as exc_info:
        await agent_service.procesar_consulta_agente(
            None, req, llm_client=llm  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.error_code == "LLM_INVALID_JSON"


async def test_agent_guardrail_hitl_no_ejecuta_mutacion_sin_confirmacion(monkeypatch):
    spy_mov = AsyncMock()
    monkeypatch.setattr(tools_service, "registrar_movimiento", spy_mov)

    llm = _FakeLLMClient(
        [
            '{"tool_name": "registrar_movimiento", "arguments": '
            '{"id_pieza": 699, "origen_id": 2, "destino_id": 1, "motivo": "Mantenimiento"}}'
        ]
    )
    req = AgentQueryRequest(
        mensaje="Mueve la pieza 699 del lab 2 al 1",
        tecnico_id=1,
        ejecutar_mutaciones=False,
    )
    resp = await agent_service.procesar_consulta_agente(
        None, req, llm_client=llm  # type: ignore[arg-type]
    )

    spy_mov.assert_not_awaited()
    assert resp.tool_calls[0].executed is False
    assert resp.tool_calls[0].requiere_confirmacion is True


async def test_agent_ejecuta_mutacion_cuando_confirmada(monkeypatch):
    mock_mov = RegistrarMovimientoResponse(
        movimiento_id=15,
        componente_id=699,
        tipo_movimiento=TipoMovimiento.TRASLADO,
        estado_componente=EstadoComponente.EN_STOCK,
        lab_origen_id=2,
        lab_destino_id=1,
        audit_log_id=88,
    )
    monkeypatch.setattr(
        tools_service, "registrar_movimiento", AsyncMock(return_value=mock_mov)
    )

    llm = _FakeLLMClient(
        [
            '{"tool_name": "registrar_movimiento", "arguments": '
            '{"id_pieza": 699, "origen_id": 2, "destino_id": 1, "motivo": "Mantenimiento"}}',
            "Traslado #15 registrado correctamente.",
        ]
    )
    req = AgentQueryRequest(
        mensaje="Confirmo mover pieza 699 del lab 2 al 1",
        tecnico_id=1,
        ejecutar_mutaciones=True,
    )
    resp = await agent_service.procesar_consulta_agente(
        None, req, llm_client=llm  # type: ignore[arg-type]
    )

    assert resp.tool_calls[0].executed is True
    assert resp.tool_calls[0].result["movimiento_id"] == 15


async def test_llm_client_not_configured_503():
    client = HttpxLLMClient(Settings(LLM_PROVIDER="gemini", LLM_API_KEY=""))
    with pytest.raises(UtilSAGError) as exc_info:
        await client.complete(
            system_prompt="sys", messages=[{"role": "user", "content": "hola"}]
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.error_code == "LLM_NOT_CONFIGURED"


async def test_api_agent_query_endpoint(monkeypatch):
    mock_resp = AgentQueryResponse(
        respuesta="Stock verificado.",
        mensaje_sanitizado="Stock de RAM",
        redacciones_aplicadas=[],
        tool_calls=[],
        reintentos_json=0,
        modelo="fake:test",
    )
    monkeypatch.setattr(
        agent_service,
        "procesar_consulta_agente",
        AsyncMock(return_value=mock_resp),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/agent/query",
            json={"mensaje": "Stock de RAM"},
        )

    assert response.status_code == 200
    assert response.json()["respuesta"] == "Stock verificado."

