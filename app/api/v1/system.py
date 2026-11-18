import secrets

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import UtilSAGError
from app.schemas.system import (
    KillSwitchStatusResponse,
    KillSwitchToggleRequest,
    TelegramDeniedAuditRequest,
    TelegramDeniedAuditResponse,
)
from app.services import audit_service, killswitch_service

router = APIRouter(prefix="/system", tags=["system"])


def _verify_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = get_settings().ADMIN_KILLSWITCH_KEY
    if not expected:
        raise UtilSAGError(
            "ADMIN_KILLSWITCH_KEY no configurada en el servidor.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="KILLSWITCH_KEY_NOT_CONFIGURED",
        )
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise UtilSAGError(
            "Credencial de administrador inválida para operar el Kill Switch.",
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="ADMIN_UNAUTHORIZED",
        )


@router.get(
    "/killswitch",
    response_model=KillSwitchStatusResponse,
    summary="Consultar estado actual del Kill Switch",
)
async def get_killswitch_endpoint() -> KillSwitchStatusResponse:
    state = killswitch_service.get_killswitch_status()
    return KillSwitchStatusResponse(
        active=state.active,
        estado_agente="INACTIVO" if state.active else "ACTIVO",
        motivo=state.motivo,
        usuario_id=state.usuario_id,
        actor_externo=state.actor_externo,
        actualizado_en=state.actualizado_en,
    )


@router.post(
    "/killswitch",
    response_model=KillSwitchStatusResponse,
    dependencies=[Depends(_verify_admin_key)],
    summary="Activar o desactivar el Kill Switch de emergencia (requiere X-Admin-Key)",
)
async def toggle_killswitch_endpoint(
    payload: KillSwitchToggleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> KillSwitchStatusResponse:
    client_ip = request.client.host if request.client else None
    state, audit_id = await killswitch_service.set_killswitch_state(
        db,
        active=payload.active,
        motivo=payload.motivo,
        usuario_id=payload.usuario_id,
        actor_externo=payload.actor_externo,
        origen=payload.origen,
        ip_address=client_ip,
    )
    return KillSwitchStatusResponse(
        active=state.active,
        estado_agente="INACTIVO" if state.active else "ACTIVO",
        motivo=state.motivo,
        usuario_id=state.usuario_id,
        actor_externo=state.actor_externo,
        actualizado_en=state.actualizado_en,
        audit_log_id=audit_id,
    )


@router.post(
    "/audit/telegram-denied",
    response_model=TelegramDeniedAuditResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar intento de acceso no autorizado en Telegram (HU-13 CA-3)",
)
async def audit_telegram_denied_endpoint(
    payload: TelegramDeniedAuditRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TelegramDeniedAuditResponse:
    username_str = f"@{payload.username}" if payload.username else "sin_username"
    actor = f"tg:{payload.telegram_user_id} ({username_str})"
    ip = payload.ip_address or (request.client.host if request.client else None)

    audit = await audit_service.registrar_audit_log(
        db,
        origen="TELEGRAM",
        accion="ACCESO_DENEGADO_WHITELIST",
        tabla_afectada="usuarios_tecnicos",
        registro_id=0,
        actor_externo=actor,
        datos_nuevos={
            "telegram_user_id": payload.telegram_user_id,
            "username": payload.username,
            "chat_id": payload.chat_id,
            "detalle": payload.detalle,
        },
        ip_address=ip,
        motivo="Intento de acceso desde Telegram User ID no incluido en Whitelist",
    )
    return TelegramDeniedAuditResponse(audit_log_id=audit.id)

