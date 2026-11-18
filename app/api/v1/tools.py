from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.enums import EstadoComponente
from app.schemas.tools import (
    ConsultarHistorialEquipoResponse,
    ConsultarStockResponse,
    RegistrarBajaComponenteRequest,
    RegistrarBajaComponenteResponse,
    RegistrarMovimientoRequest,
    RegistrarMovimientoResponse,
)
from app.services import tools_service

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get(
    "/stock",
    response_model=ConsultarStockResponse,
    summary="Consultar stock de componentes",
)
async def consultar_stock_endpoint(
    tipo_pieza: str | None = Query(
        default=None, description="Categoría o tipo de pieza (ej. RAM, Storage, PSU)"
    ),
    ubicacion: str | None = Query(
        default=None, description="Nombre o filtro de laboratorio/almacén"
    ),
    estado: EstadoComponente | None = Query(
        default=EstadoComponente.EN_STOCK,
        description="Estado del componente (por defecto EN_STOCK)",
    ),
    db: AsyncSession = Depends(get_db),
) -> ConsultarStockResponse:
    return await tools_service.consultar_stock(
        db, tipo_pieza=tipo_pieza, ubicacion=ubicacion, estado=estado
    )


@router.get(
    "/equipos/{codigo_patrimonial}/historial",
    response_model=ConsultarHistorialEquipoResponse,
    summary="Consultar hoja de vida e historial de un equipo",
)
async def consultar_historial_equipo_endpoint(
    codigo_patrimonial: str,
    db: AsyncSession = Depends(get_db),
) -> ConsultarHistorialEquipoResponse:
    return await tools_service.consultar_historial_equipo(
        db, codigo_patrimonial=codigo_patrimonial
    )


@router.post(
    "/movimientos",
    response_model=RegistrarMovimientoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar traslado/movimiento de componente (SERIALIZABLE + FOR UPDATE)",
)
async def registrar_movimiento_endpoint(
    payload: RegistrarMovimientoRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegistrarMovimientoResponse:
    client_ip = request.client.host if request.client else None
    return await tools_service.registrar_movimiento(db, payload, ip_address=client_ip)


@router.post(
    "/bajas",
    response_model=RegistrarBajaComponenteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar propuesta técnica preliminar de baja de componente",
)
async def registrar_baja_componente_endpoint(
    payload: RegistrarBajaComponenteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegistrarBajaComponenteResponse:
    client_ip = request.client.host if request.client else None
    return await tools_service.registrar_baja_componente(
        db, payload, ip_address=client_ip
    )

