from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.agent import (
    AgentQueryRequest,
    AgentQueryResponse,
    VisionOCRRequest,
    VisionOCRResponse,
)
from app.services import agent_service, vision_service

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post(
    "/query",
    response_model=AgentQueryResponse,
    summary="Orquestador LLM con Function Calling, Data Masking y Guardrails",
)
async def agent_query_endpoint(
    payload: AgentQueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentQueryResponse:
    client_ip = request.client.host if request.client else None
    return await agent_service.procesar_consulta_agente(
        db, payload, ip_address=client_ip
    )


@router.post(
    "/vision-ocr",
    response_model=VisionOCRResponse,
    summary="OCR multimodal de placas SBN con umbral de confianza >= 90% (HU-15 CA-2/3)",
)
async def vision_ocr_endpoint(payload: VisionOCRRequest) -> VisionOCRResponse:
    return await vision_service.analizar_placa_ocr(payload)

