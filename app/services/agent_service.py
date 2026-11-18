import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UtilSAGError
from app.models.enums import EstadoComponente
from app.schemas.agent import (
    AgentQueryRequest,
    AgentQueryResponse,
    ToolCallExecution,
)
from app.schemas.tools import (
    RegistrarBajaComponenteRequest,
    RegistrarMovimientoRequest,
)
from app.services import tools_service
from app.services.killswitch_service import ensure_agent_active
from app.services.llm_client import HttpxLLMClient, LLMClient, SYSTEM_PROMPT
from app.services.privacy_service import mask_pii

ToolName = Literal[
    "consultar_stock",
    "consultar_historial_equipo",
    "registrar_movimiento",
    "registrar_baja_componente",
]

_MUTATION_TOOLS = {"registrar_movimiento", "registrar_baja_componente"}


class _ConsultarStockArgs(BaseModel):
    tipo_pieza: str | None = None
    ubicacion: str | None = None
    estado: EstadoComponente | None = EstadoComponente.EN_STOCK


class _ConsultarHistorialArgs(BaseModel):
    codigo_patrimonial: str = Field(..., min_length=1)


class _LLMToolEnvelope(BaseModel):
    tool_name: ToolName | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reply: str | None = None


def _strip_markdown_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _validate_tool_call(
    raw_content: str,
    request: AgentQueryRequest,
) -> tuple[_LLMToolEnvelope, BaseModel | None]:
    cleaned = _strip_markdown_fences(raw_content)
    parsed = json.loads(cleaned)
    envelope = _LLMToolEnvelope.model_validate(parsed)

    if envelope.tool_name is None:
        return envelope, None

    args = dict(envelope.arguments)
    if envelope.tool_name in _MUTATION_TOOLS:
        if "tecnico_id" not in args and request.tecnico_id is not None:
            args["tecnico_id"] = request.tecnico_id
        args.setdefault("origen_canal", request.origen_canal)
        if request.actor_externo and "actor_externo" not in args:
            args["actor_externo"] = request.actor_externo

    validator_map: dict[str, type[BaseModel]] = {
        "consultar_stock": _ConsultarStockArgs,
        "consultar_historial_equipo": _ConsultarHistorialArgs,
        "registrar_movimiento": RegistrarMovimientoRequest,
        "registrar_baja_componente": RegistrarBajaComponenteRequest,
    }
    validated_args = validator_map[envelope.tool_name].model_validate(args)
    return envelope, validated_args


async def _execute_tool(
    db: AsyncSession,
    *,
    tool_name: ToolName,
    validated_args: BaseModel,
    ejecutar_mutaciones: bool,
    ip_address: str | None,
) -> ToolCallExecution:
    args_dict = validated_args.model_dump(mode="json")

    if tool_name in _MUTATION_TOOLS and not ejecutar_mutaciones:
        return ToolCallExecution(
            tool_name=tool_name,
            arguments=args_dict,
            executed=False,
            requiere_confirmacion=True,
            result={
                "mensaje": (
                    "Operación validada. Requiere confirmación explícita Human-in-the-Loop "
                    "antes de modificar la base de datos."
                )
            },
        )

    if tool_name == "consultar_stock":
        assert isinstance(validated_args, _ConsultarStockArgs)
        res = await tools_service.consultar_stock(
            db,
            tipo_pieza=validated_args.tipo_pieza,
            ubicacion=validated_args.ubicacion,
            estado=validated_args.estado,
        )
    elif tool_name == "consultar_historial_equipo":
        assert isinstance(validated_args, _ConsultarHistorialArgs)
        res = await tools_service.consultar_historial_equipo(
            db, codigo_patrimonial=validated_args.codigo_patrimonial
        )
    elif tool_name == "registrar_movimiento":
        assert isinstance(validated_args, RegistrarMovimientoRequest)
        res = await tools_service.registrar_movimiento(
            db, validated_args, ip_address=ip_address
        )
    else:
        assert isinstance(validated_args, RegistrarBajaComponenteRequest)
        res = await tools_service.registrar_baja_componente(
            db, validated_args, ip_address=ip_address
        )

    return ToolCallExecution(
        tool_name=tool_name,
        arguments=args_dict,
        executed=True,
        requiere_confirmacion=False,
        result=res.model_dump(mode="json"),
    )


async def procesar_consulta_agente(
    db: AsyncSession,
    payload: AgentQueryRequest,
    *,
    llm_client: LLMClient | None = None,
    ip_address: str | None = None,
) -> AgentQueryResponse:
    """Orquesta una consulta en lenguaje natural con Data Masking, Function Calling y reintento JSON."""
    ensure_agent_active()
    settings = get_settings()
    client = llm_client or HttpxLLMClient(settings)

    mask_result = mask_pii(payload.mensaje)
    messages: list[dict[str, str]] = [
        {"role": "user", "content": mask_result.masked_text}
    ]

    max_retries = max(0, settings.LLM_MAX_RETRIES)
    envelope: _LLMToolEnvelope | None = None
    validated_args: BaseModel | None = None
    reintentos_json = 0

    for attempt in range(max_retries + 1):
        llm_res = await client.complete(
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
            json_mode=True,
        )
        try:
            envelope, validated_args = _validate_tool_call(llm_res.content, payload)
            break
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if attempt >= max_retries:
                raise UtilSAGError(
                    f"El modelo no produjo un JSON de herramienta válido tras {max_retries + 1} intentos.",
                    status_code=502,
                    error_code="LLM_INVALID_JSON",
                ) from exc
            reintentos_json += 1
            messages.append({"role": "assistant", "content": llm_res.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"El JSON anterior es inválido ({exc}). "
                        "Corrige el formato y devuelve únicamente un JSON válido según el esquema."
                    ),
                }
            )

    assert envelope is not None
    if envelope.tool_name is None or validated_args is None:
        return AgentQueryResponse(
            respuesta=envelope.reply
            or "No se identificó una operación de inventario aplicable.",
            mensaje_sanitizado=mask_result.masked_text,
            redacciones_aplicadas=mask_result.redactions,
            tool_calls=[],
            reintentos_json=reintentos_json,
            modelo=client.model_name,
        )

    execution = await _execute_tool(
        db,
        tool_name=envelope.tool_name,
        validated_args=validated_args,
        ejecutar_mutaciones=payload.ejecutar_mutaciones,
        ip_address=ip_address,
    )

    if execution.requiere_confirmacion:
        respuesta = (
            f"Propuesta preparada para `{execution.tool_name}` con parámetros validados. "
            "Confirma explícitamente la operación para aplicarla."
        )
    else:
        synthesis_messages = [
            {
                "role": "user",
                "content": (
                    f"Solicitud del técnico: {mask_result.masked_text}\n"
                    f"Resultado real de la herramienta `{execution.tool_name}` (JSON):\n"
                    f"{json.dumps(execution.result, ensure_ascii=False)}\n\n"
                    "Redacta una respuesta breve y precisa en español basada ÚNICAMENTE en ese resultado real."
                ),
            }
        ]
        final_llm = await client.complete(
            system_prompt=SYSTEM_PROMPT,
            messages=synthesis_messages,
            json_mode=False,
        )
        respuesta = final_llm.content.strip()

    return AgentQueryResponse(
        respuesta=respuesta,
        mensaje_sanitizado=mask_result.masked_text,
        redacciones_aplicadas=mask_result.redactions,
        tool_calls=[execution],
        reintentos_json=reintentos_json,
        modelo=client.model_name,
    )

