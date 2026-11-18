import base64
import binascii
import json

from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.core.exceptions import UtilSAGError
from app.schemas.agent import VisionOCRRequest, VisionOCRResponse
from app.services.agent_service import _strip_markdown_fences
from app.services.killswitch_service import ensure_agent_active
from app.services.llm_client import HttpxLLMClient, LLMClient

_VISION_SYSTEM_PROMPT = """Eres un módulo OCR de precisión para placas patrimoniales SBN (Perú) y números de serie de hardware (FISI-UNMSM).
Responde ÚNICAMENTE con un objeto JSON válido con esta estructura exacta:
{
  "codigo_sbn": "<código patrimonial SBN completo de 12 dígitos sin guiones, o null si no es legible>",
  "numero_serie": "<número de serie S/N explícito del fabricante si es visible, o null>",
  "confianza": <número decimal entre 0.0 y 1.0>,
  "observacion": "<breve nota sobre legibilidad o deterioro>"
}
REGLAS:
- Las placas patrimoniales SBN tienen 12 dígitos (a veces impresas como 8 dígitos + guion + 4 dígitos, ej. 46225115-4696). Esos 4 dígitos finales SON parte del código SBN, NO son número de serie; devuélvelos juntos sin guiones (ej. "462251154696").
- No inventes dígitos ni letras que no se vean con claridad en la imagen.
- Si la etiqueta está borrosa, cortada o con reflejos, asigna una confianza inferior a 0.90.
"""

MIN_OCR_CONFIDENCE = 0.90


class _VisionPayload(BaseModel):
    codigo_sbn: str | None = None
    numero_serie: str | None = None
    confianza: float = Field(..., ge=0.0, le=1.0)
    observacion: str | None = None


async def analizar_placa_ocr(
    payload: VisionOCRRequest,
    *,
    llm_client: LLMClient | None = None,
) -> VisionOCRResponse:
    """Extrae código SBN / serie desde una imagen mediante visión LLM con guardrail >= 90%."""
    ensure_agent_active()

    try:
        base64.b64decode(payload.imagen_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UtilSAGError(
            "La imagen proporcionada no es un Base64 válido.",
            status_code=422,
            error_code="INVALID_IMAGE_BASE64",
        ) from exc

    settings = get_settings()
    client = llm_client or HttpxLLMClient(settings)

    llm_res = await client.complete_vision(
        system_prompt=_VISION_SYSTEM_PROMPT,
        prompt="Extrae el código patrimonial SBN y número de serie de esta etiqueta.",
        image_base64=payload.imagen_base64,
        mime_type=payload.mime_type,
    )

    try:
        cleaned = _strip_markdown_fences(llm_res.content)
        parsed = _VisionPayload.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise UtilSAGError(
            "El módulo de visión no devolvió un JSON válido.",
            status_code=502,
            error_code="VISION_INVALID_JSON",
        ) from exc

    codigo = parsed.codigo_sbn.strip() if parsed.codigo_sbn else None
    serie = parsed.numero_serie.strip() if parsed.numero_serie else None

    if codigo:
        codigo_digits = codigo.replace("-", "").replace(" ", "")
        if len(codigo_digits) == 8 and codigo_digits.isdigit() and serie and len(serie) == 4 and serie.isdigit():
            codigo = f"{codigo_digits}{serie}"
            serie = None
        else:
            codigo = codigo_digits if codigo_digits.isdigit() else codigo

    requiere_reintento = parsed.confianza < MIN_OCR_CONFIDENCE or codigo is None

    return VisionOCRResponse(
        codigo_sbn=codigo,
        numero_serie=serie,
        confianza=parsed.confianza,
        requiere_reintento=requiere_reintento,
        observacion=parsed.observacion,
        modelo=llm_res.model,
    )

