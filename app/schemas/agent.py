from typing import Any

from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    mensaje: str = Field(
        ...,
        min_length=2,
        max_length=2000,
        description="Consulta o instrucción en lenguaje natural del técnico",
    )
    tecnico_id: int | None = Field(
        default=None,
        gt=0,
        description="ID del técnico autenticado en usuarios_tecnicos",
    )
    origen_canal: str = Field(default="API", min_length=2, max_length=30)
    actor_externo: str | None = Field(default=None, max_length=100)
    ejecutar_mutaciones: bool = Field(
        default=False,
        description=(
            "Guardrail HITL: si es False, las operaciones de traslado o baja retornan "
            "una propuesta validada para confirmación explícita sin mutar la BD."
        ),
    )


class ToolCallExecution(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    executed: bool
    requiere_confirmacion: bool = False
    result: dict[str, Any] | None = None


class AgentQueryResponse(BaseModel):
    respuesta: str
    mensaje_sanitizado: str
    redacciones_aplicadas: list[str]
    tool_calls: list[ToolCallExecution]
    reintentos_json: int = 0
    modelo: str


class VisionOCRRequest(BaseModel):
    imagen_base64: str = Field(..., min_length=20, description="Imagen codificada en Base64")
    mime_type: str = Field(default="image/jpeg", pattern=r"^image/(jpeg|png|webp)$")


class VisionOCRResponse(BaseModel):
    codigo_sbn: str | None = None
    numero_serie: str | None = None
    confianza: float = Field(..., ge=0.0, le=1.0)
    requiere_reintento: bool
    observacion: str | None = None
    modelo: str

