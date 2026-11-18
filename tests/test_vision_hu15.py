import base64
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import UtilSAGError
from app.main import app
from app.schemas.agent import VisionOCRRequest, VisionOCRResponse
from app.services import vision_service
from app.services.llm_client import LLMCallResult


class _FakeVisionLLM:
    def __init__(self, content: str):
        self._content = content

    @property
    def model_name(self) -> str:
        return "fake:vision"

    async def complete(self, *, system_prompt, messages, json_mode=False):
        return LLMCallResult(content="", model=self.model_name)

    async def complete_vision(
        self, *, system_prompt, prompt, image_base64, mime_type="image/jpeg"
    ):
        return LLMCallResult(content=self._content, model=self.model_name)


_VALID_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 30).decode("ascii")


async def test_vision_ocr_confianza_alta_ok():
    llm = _FakeVisionLLM(
        '{"codigo_sbn": "740868655163", "numero_serie": "SN-123", "confianza": 0.96, "observacion": "Nítida"}'
    )
    res = await vision_service.analizar_placa_ocr(
        VisionOCRRequest(imagen_base64=_VALID_B64), llm_client=llm
    )
    assert res.codigo_sbn == "740868655163"
    assert res.confianza == 0.96
    assert res.requiere_reintento is False


async def test_vision_ocr_confianza_baja_pide_reintento():
    llm = _FakeVisionLLM(
        '{"codigo_sbn": "740868655163", "numero_serie": null, "confianza": 0.72, "observacion": "Borrosa"}'
    )
    res = await vision_service.analizar_placa_ocr(
        VisionOCRRequest(imagen_base64=_VALID_B64), llm_client=llm
    )
    assert res.requiere_reintento is True


async def test_vision_ocr_normaliza_sbn_8_mas_4_digitos():
    llm = _FakeVisionLLM(
        '{"codigo_sbn": "46225115", "numero_serie": "4696", "confianza": 0.95, "observacion": "Placa SBN"}'
    )
    res = await vision_service.analizar_placa_ocr(
        VisionOCRRequest(imagen_base64=_VALID_B64), llm_client=llm
    )
    assert res.codigo_sbn == "462251154696"
    assert res.numero_serie is None


async def test_vision_ocr_base64_invalido_422():
    with pytest.raises(UtilSAGError) as exc_info:
        await vision_service.analizar_placa_ocr(
            VisionOCRRequest(imagen_base64="no_es_base64_valido_!!!")
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == "INVALID_IMAGE_BASE64"


async def test_api_vision_ocr_endpoint(monkeypatch):
    mock_resp = VisionOCRResponse(
        codigo_sbn="740868655163",
        numero_serie=None,
        confianza=0.95,
        requiere_reintento=False,
        observacion="OK",
        modelo="fake:vision",
    )
    monkeypatch.setattr(
        vision_service, "analizar_placa_ocr", AsyncMock(return_value=mock_resp)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/agent/vision-ocr",
            json={"imagen_base64": _VALID_B64, "mime_type": "image/jpeg"},
        )

    assert resp.status_code == 200
    assert resp.json()["codigo_sbn"] == "740868655163"

