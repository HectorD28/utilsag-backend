from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UtilSAGError
from app.services.audit_service import registrar_audit_log


@dataclass
class KillSwitchState:
    active: bool = False
    motivo: str | None = None
    usuario_id: int | None = None
    actor_externo: str | None = None
    actualizado_en: datetime | None = None


_state = KillSwitchState()


def get_killswitch_status() -> KillSwitchState:
    return KillSwitchState(
        active=_state.active,
        motivo=_state.motivo,
        usuario_id=_state.usuario_id,
        actor_externo=_state.actor_externo,
        actualizado_en=_state.actualizado_en,
    )


def reset_killswitch_for_tests() -> None:
    global _state
    _state = KillSwitchState()


def ensure_agent_active() -> None:
    """Bloquea operaciones del agente y mutaciones si el Kill Switch está activo."""
    if _state.active:
        raise UtilSAGError(
            f"Sistema detenido por Kill Switch de emergencia. Motivo: {_state.motivo or 'No especificado'}",
            status_code=503,
            error_code="KILLSWITCH_ACTIVE",
        )


async def set_killswitch_state(
    db: AsyncSession,
    *,
    active: bool,
    motivo: str,
    usuario_id: int | None = None,
    actor_externo: str | None = None,
    origen: str = "API",
    ip_address: str | None = None,
) -> tuple[KillSwitchState, int]:
    """Actualiza el estado global del Kill Switch y registra auditoría inmutable en audit_logs."""
    anterior = {
        "active": _state.active,
        "motivo": _state.motivo,
        "usuario_id": _state.usuario_id,
        "actor_externo": _state.actor_externo,
    }

    now = datetime.now(timezone.utc)
    _state.active = active
    _state.motivo = motivo
    _state.usuario_id = usuario_id
    _state.actor_externo = actor_externo
    _state.actualizado_en = now

    accion = "KILLSWITCH_ACTIVADO" if active else "KILLSWITCH_DESACTIVADO"
    audit = await registrar_audit_log(
        db,
        origen=origen,
        accion=accion,
        tabla_afectada="system_killswitch",
        registro_id=1 if active else 0,
        usuario_id=usuario_id,
        actor_externo=actor_externo,
        datos_anteriores=anterior,
        datos_nuevos={
            "active": active,
            "estado_agente": "INACTIVO" if active else "ACTIVO",
            "motivo": motivo,
            "actualizado_en": now.isoformat(),
        },
        ip_address=ip_address,
        motivo=motivo,
    )

    return get_killswitch_status(), audit.id

