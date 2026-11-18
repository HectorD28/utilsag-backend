from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleViolationError,
    ConcurrencyConflictError,
    ResourceNotFoundError,
)
from app.models.enums import EstadoComponente, EstadoEquipo, TipoMovimiento
from app.models.inventory import (
    Baja,
    Componente,
    Equipo,
    Laboratorio,
    MovimientoHistorial,
    UsuarioTecnico,
)
from app.schemas.tools import (
    ComponenteEquipoItem,
    ConsultarHistorialEquipoResponse,
    ConsultarStockResponse,
    EquipoDetalle,
    MovimientoEquipoItem,
    RegistrarBajaComponenteRequest,
    RegistrarBajaComponenteResponse,
    RegistrarMovimientoRequest,
    RegistrarMovimientoResponse,
    StockComponenteItem,
    StockResumenItem,
)
from app.services.audit_service import registrar_audit_log
from app.services.killswitch_service import ensure_agent_active

_SERIALIZATION_SQLSTATES = {"40001", "40P01"}


def _is_serialization_error(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _SERIALIZATION_SQLSTATES


async def _validar_tecnico_activo(db: AsyncSession, tecnico_id: int) -> UsuarioTecnico:
    tecnico = await db.get(UsuarioTecnico, tecnico_id)
    if tecnico is None:
        raise ResourceNotFoundError(
            f"Técnico con ID {tecnico_id} no encontrado.",
            error_code="TECNICO_NOT_FOUND",
        )
    if not tecnico.activo:
        raise BusinessRuleViolationError(
            f"El técnico con ID {tecnico_id} se encuentra inactivo.",
            error_code="TECNICO_INACTIVO",
        )
    return tecnico


def _determinar_tipo_movimiento(
    equipo_origen_id: int | None,
    equipo_destino_id: int | None,
) -> TipoMovimiento:
    if equipo_origen_id is None and equipo_destino_id is not None:
        return TipoMovimiento.ASIGNACION
    if equipo_origen_id is not None and equipo_destino_id is None:
        return TipoMovimiento.EXTRACCION
    if equipo_origen_id is not None and equipo_destino_id is not None:
        return TipoMovimiento.REEMPLAZO
    return TipoMovimiento.TRASLADO


async def consultar_stock(
    db: AsyncSession,
    *,
    tipo_pieza: str | None = None,
    ubicacion: str | None = None,
    estado: EstadoComponente | None = EstadoComponente.EN_STOCK,
) -> ConsultarStockResponse:
    """Consulta de solo lectura sobre existencias de componentes por tipo y ubicación."""
    stmt = (
        select(Componente, Laboratorio.nombre.label("laboratorio_nombre"))
        .join(Laboratorio, Componente.laboratorio_actual_id == Laboratorio.id)
        .order_by(Laboratorio.nombre, Componente.tipo, Componente.id)
    )

    if tipo_pieza:
        stmt = stmt.where(Componente.tipo.ilike(f"%{tipo_pieza.strip()}%"))
    if ubicacion:
        stmt = stmt.where(Laboratorio.nombre.ilike(f"%{ubicacion.strip()}%"))
    if estado is not None:
        stmt = stmt.where(Componente.estado == estado)

    rows = (await db.execute(stmt)).all()

    items: list[StockComponenteItem] = []
    resumen_counts: dict[tuple[str, int, str], int] = defaultdict(int)

    for comp, lab_nombre in rows:
        comp_estado = comp.estado or EstadoComponente.EN_STOCK
        items.append(
            StockComponenteItem(
                id=comp.id,
                tipo=comp.tipo,
                descripcion=comp.descripcion,
                estado=comp_estado,
                codigo_patrimonial=comp.codigo_patrimonial,
                numero_serie=comp.numero_serie,
                es_periferico=bool(comp.es_periferico),
                laboratorio_id=comp.laboratorio_actual_id,
                laboratorio_nombre=lab_nombre,
                equipo_id=comp.equipo_id,
            )
        )
        resumen_counts[(comp.tipo, comp.laboratorio_actual_id, lab_nombre)] += 1

    resumen = [
        StockResumenItem(
            tipo=tipo,
            laboratorio_id=lab_id,
            laboratorio_nombre=lab_nombre,
            cantidad=cantidad,
        )
        for (tipo, lab_id, lab_nombre), cantidad in sorted(resumen_counts.items())
    ]

    return ConsultarStockResponse(total=len(items), resumen=resumen, items=items)


async def consultar_historial_equipo(
    db: AsyncSession,
    *,
    codigo_patrimonial: str,
) -> ConsultarHistorialEquipoResponse:
    """Recupera la hoja de vida, componentes instalados e historial de movimientos de un equipo."""
    codigo = codigo_patrimonial.strip()
    stmt_equipo = (
        select(Equipo, Laboratorio.nombre.label("laboratorio_nombre"))
        .join(Laboratorio, Equipo.laboratorio_id == Laboratorio.id)
        .where(Equipo.codigo_patrimonial == codigo)
    )
    row = (await db.execute(stmt_equipo)).first()
    if row is None:
        raise ResourceNotFoundError(
            f"Equipo con código patrimonial '{codigo}' no encontrado.",
            error_code="EQUIPO_NOT_FOUND",
        )

    equipo, lab_nombre = row

    stmt_comps = (
        select(Componente)
        .where(Componente.equipo_id == equipo.id)
        .order_by(Componente.tipo, Componente.id)
    )
    componentes = (await db.execute(stmt_comps)).scalars().all()

    stmt_movs = (
        select(MovimientoHistorial)
        .where(
            or_(
                MovimientoHistorial.equipo_origen_id == equipo.id,
                MovimientoHistorial.equipo_destino_id == equipo.id,
            )
        )
        .order_by(
            MovimientoHistorial.fecha_movimiento.desc(),
            MovimientoHistorial.id.desc(),
        )
    )
    movimientos = (await db.execute(stmt_movs)).scalars().all()

    return ConsultarHistorialEquipoResponse(
        equipo=EquipoDetalle(
            id=equipo.id,
            codigo_patrimonial=equipo.codigo_patrimonial,
            nombre_identificador=equipo.nombre_identificador,
            tipo=equipo.tipo,
            estado=equipo.estado,
            laboratorio_id=equipo.laboratorio_id,
            laboratorio_nombre=lab_nombre,
            creado_en=equipo.creado_en,
        ),
        componentes_instalados=[
            ComponenteEquipoItem.model_validate(c) for c in componentes
        ],
        historial_movimientos=[
            MovimientoEquipoItem.model_validate(m) for m in movimientos
        ],
    )


async def registrar_movimiento(
    db: AsyncSession,
    payload: RegistrarMovimientoRequest,
    *,
    ip_address: str | None = None,
) -> RegistrarMovimientoResponse:
    """Ejecuta un traslado/asignación/extracción de componente con bloqueo SELECT FOR UPDATE."""
    ensure_agent_active()
    try:
        await _validar_tecnico_activo(db, payload.tecnico_id)

        comp = (
            await db.execute(
                select(Componente)
                .where(Componente.id == payload.id_pieza)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if comp is None:
            raise ResourceNotFoundError(
                f"Componente con ID {payload.id_pieza} no encontrado.",
                error_code="COMPONENTE_NOT_FOUND",
            )

        if comp.estado == EstadoComponente.DADO_DE_BAJA:
            raise BusinessRuleViolationError(
                "No se puede mover un componente con estado DADO_DE_BAJA.",
                error_code="COMPONENTE_DADO_DE_BAJA",
            )

        if comp.laboratorio_actual_id != payload.origen_id:
            raise ConcurrencyConflictError(
                f"El componente {comp.id} ya no se encuentra en el laboratorio origen {payload.origen_id} "
                f"(ubicación actual: {comp.laboratorio_actual_id}).",
                error_code="ORIGEN_MISMATCH",
            )

        lab_destino = await db.get(Laboratorio, payload.destino_id)
        if lab_destino is None:
            raise ResourceNotFoundError(
                f"Laboratorio destino con ID {payload.destino_id} no encontrado.",
                error_code="LABORATORIO_NOT_FOUND",
            )

        if (
            payload.origen_id == payload.destino_id
            and payload.equipo_destino_id == comp.equipo_id
        ):
            raise BusinessRuleViolationError(
                "El origen y el destino del movimiento son idénticos.",
                error_code="MOVIMIENTO_REDUNDANTE",
            )

        if payload.equipo_destino_id is not None:
            equipo_dest = (
                await db.execute(
                    select(Equipo)
                    .where(Equipo.id == payload.equipo_destino_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if equipo_dest is None:
                raise ResourceNotFoundError(
                    f"Equipo destino con ID {payload.equipo_destino_id} no encontrado.",
                    error_code="EQUIPO_NOT_FOUND",
                )
            if equipo_dest.estado == EstadoEquipo.DADO_DE_BAJA:
                raise BusinessRuleViolationError(
                    "No se puede instalar un componente en un equipo DADO_DE_BAJA.",
                    error_code="EQUIPO_DADO_DE_BAJA",
                )
            if equipo_dest.laboratorio_id != payload.destino_id:
                raise BusinessRuleViolationError(
                    f"El equipo destino {equipo_dest.id} pertenece al laboratorio {equipo_dest.laboratorio_id}, "
                    f"no al laboratorio destino {payload.destino_id}.",
                    error_code="EQUIPO_LABORATORIO_MISMATCH",
                )

        equipo_origen_id = comp.equipo_id
        estado_anterior = comp.estado
        datos_anteriores = {
            "laboratorio_actual_id": comp.laboratorio_actual_id,
            "equipo_id": comp.equipo_id,
            "estado": estado_anterior.value if estado_anterior else None,
        }

        tipo_mov = _determinar_tipo_movimiento(
            equipo_origen_id, payload.equipo_destino_id
        )

        if payload.equipo_destino_id is not None:
            nuevo_estado = EstadoComponente.INSTALADO
        elif estado_anterior == EstadoComponente.DEFECTUOSO:
            nuevo_estado = EstadoComponente.DEFECTUOSO
        else:
            nuevo_estado = EstadoComponente.EN_STOCK

        comp.laboratorio_actual_id = payload.destino_id
        comp.equipo_id = payload.equipo_destino_id
        comp.estado = nuevo_estado

        movimiento = MovimientoHistorial(
            componente_id=comp.id,
            equipo_origen_id=equipo_origen_id,
            equipo_destino_id=payload.equipo_destino_id,
            lab_origen_id=payload.origen_id,
            lab_destino_id=payload.destino_id,
            tecnico_id=payload.tecnico_id,
            tipo_movimiento=tipo_mov,
            motivo=payload.motivo,
        )
        db.add(movimiento)
        await db.flush()

        audit = await registrar_audit_log(
            db,
            origen=payload.origen_canal,
            accion="TRASLADO_COMPONENTE",
            tabla_afectada="componentes",
            registro_id=comp.id,
            usuario_id=payload.tecnico_id,
            actor_externo=payload.actor_externo,
            datos_anteriores=datos_anteriores,
            datos_nuevos={
                "laboratorio_actual_id": comp.laboratorio_actual_id,
                "equipo_id": comp.equipo_id,
                "estado": nuevo_estado.value,
                "movimiento_id": movimiento.id,
                "tipo_movimiento": tipo_mov.value,
            },
            ip_address=ip_address,
            motivo=payload.motivo,
        )

        return RegistrarMovimientoResponse(
            movimiento_id=movimiento.id,
            componente_id=comp.id,
            tipo_movimiento=tipo_mov,
            estado_componente=nuevo_estado,
            lab_origen_id=payload.origen_id,
            lab_destino_id=payload.destino_id,
            equipo_origen_id=equipo_origen_id,
            equipo_destino_id=payload.equipo_destino_id,
            fecha_movimiento=movimiento.fecha_movimiento,
            audit_log_id=audit.id,
        )
    except DBAPIError as exc:
        if _is_serialization_error(exc):
            raise ConcurrencyConflictError() from exc
        raise


async def registrar_baja_componente(
    db: AsyncSession,
    payload: RegistrarBajaComponenteRequest,
    *,
    ip_address: str | None = None,
) -> RegistrarBajaComponenteResponse:
    """Registra una propuesta técnica preliminar de baja bloqueando la baja patrimonial definitiva automática."""
    ensure_agent_active()
    if payload.baja_patrimonial_definitiva:
        raise BusinessRuleViolationError(
            "Operación bloqueada por normativa (Directiva N.° 0006-2021-EF/54.01): "
            "no se permite ejecutar bajas patrimoniales definitivas automáticas; "
            "solo se registran propuestas técnicas preliminares para USGOM.",
            error_code="BAJA_DEFINITIVA_BLOQUEADA",
        )

    try:
        await _validar_tecnico_activo(db, payload.tecnico_id)

        comp = (
            await db.execute(
                select(Componente)
                .where(Componente.id == payload.id_pieza)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if comp is None:
            raise ResourceNotFoundError(
                f"Componente con ID {payload.id_pieza} no encontrado.",
                error_code="COMPONENTE_NOT_FOUND",
            )

        if comp.estado == EstadoComponente.DADO_DE_BAJA:
            raise BusinessRuleViolationError(
                "El componente ya se encuentra con estado DADO_DE_BAJA.",
                error_code="COMPONENTE_YA_DADO_DE_BAJA",
            )

        datos_anteriores = {
            "equipo_id": comp.equipo_id,
            "estado": comp.estado.value if comp.estado else None,
            "codigo_patrimonial": comp.codigo_patrimonial,
        }

        comp.equipo_id = None
        comp.estado = EstadoComponente.DEFECTUOSO

        baja = Baja(
            componente_id=comp.id,
            equipo_id=None,
            tecnico_id=payload.tecnico_id,
            motivo_falla=payload.motivo_falla,
        )
        db.add(baja)
        await db.flush()

        audit = await registrar_audit_log(
            db,
            origen=payload.origen_canal,
            accion="PROPUESTA_BAJA_COMPONENTE",
            tabla_afectada="bajas",
            registro_id=baja.id,
            usuario_id=payload.tecnico_id,
            actor_externo=payload.actor_externo,
            datos_anteriores=datos_anteriores,
            datos_nuevos={
                "baja_id": baja.id,
                "componente_id": comp.id,
                "equipo_id": None,
                "estado": EstadoComponente.DEFECTUOSO.value,
                "estado_patrimonial": "PENDIENTE_USGOM",
            },
            ip_address=ip_address,
            motivo=payload.motivo_falla,
        )

        return RegistrarBajaComponenteResponse(
            baja_id=baja.id,
            componente_id=comp.id,
            estado_componente=EstadoComponente.DEFECTUOSO,
            estado_patrimonial="PENDIENTE_USGOM",
            fecha_baja=baja.fecha_baja,
            audit_log_id=audit.id,
            mensaje=(
                "Propuesta técnica de baja registrada (estado DEFECTUOSO). "
                "Requiere trámite y resolución formal ante USGOM (Directiva N.° 0006-2021-EF/54.01)."
            ),
        )
    except DBAPIError as exc:
        if _is_serialization_error(exc):
            raise ConcurrencyConflictError() from exc
        raise

