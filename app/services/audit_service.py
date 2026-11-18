from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import AuditLog


async def registrar_audit_log(
    db: AsyncSession,
    *,
    origen: str,
    accion: str,
    tabla_afectada: str,
    registro_id: int,
    usuario_id: int | None = None,
    actor_externo: str | None = None,
    datos_anteriores: dict[str, Any] | None = None,
    datos_nuevos: dict[str, Any] | None = None,
    ip_address: str | None = None,
    motivo: str | None = None,
) -> AuditLog:
    """Inserta un registro inmutable en `audit_logs` dentro de la transacción activa."""
    log = AuditLog(
        usuario_id=usuario_id,
        actor_externo=actor_externo,
        origen=origen,
        accion=accion,
        tabla_afectada=tabla_afectada,
        registro_id=registro_id,
        datos_anteriores=datos_anteriores,
        datos_nuevos=datos_nuevos,
        ip_address=ip_address,
        motivo=motivo,
    )
    db.add(log)
    await db.flush()
    return log

