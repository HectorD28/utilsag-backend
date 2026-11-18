from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import (
    BusinessRuleViolationError,
    ConcurrencyConflictError,
    ResourceNotFoundError,
)
from app.models.enums import EstadoComponente, EstadoEquipo, TipoMovimiento
from app.models.inventory import (
    AuditLog,
    Baja,
    Componente,
    Equipo,
    Laboratorio,
    MovimientoHistorial,
    UsuarioTecnico,
)
from app.schemas.tools import (
    RegistrarBajaComponenteRequest,
    RegistrarMovimientoRequest,
)
from app.services import tools_service


class _FakeResult:
    def __init__(self, *, rows=None, scalar=None, scalars_list=None):
        self._rows = rows or []
        self._scalar = scalar
        self._scalars_list = scalars_list or []

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars_list)


def _make_db_session(get_map=None, execute_results=None):
    get_map = get_map or {}
    execute_queue = list(execute_results or [])
    added = []
    next_id = 100

    async def fake_get(model, pk):
        return get_map.get((model, pk))

    async def fake_execute(_stmt):
        return execute_queue.pop(0)

    def fake_add(obj):
        added.append(obj)

    async def fake_flush():
        nonlocal next_id
        for obj in added:
            if getattr(obj, "id", None) is None:
                next_id += 1
                obj.id = next_id

    db = MagicMock()
    db.get = AsyncMock(side_effect=fake_get)
    db.execute = AsyncMock(side_effect=fake_execute)
    db.add = MagicMock(side_effect=fake_add)
    db.flush = AsyncMock(side_effect=fake_flush)
    db._added = added
    return db


async def test_consultar_stock_agrupa_resumen():
    comp1 = Componente(
        id=1,
        tipo="RAM",
        descripcion="16GB DDR4",
        estado=EstadoComponente.EN_STOCK,
        laboratorio_actual_id=1,
        es_periferico=False,
    )
    comp2 = Componente(
        id=2,
        tipo="RAM",
        descripcion="8GB DDR4",
        estado=EstadoComponente.EN_STOCK,
        laboratorio_actual_id=1,
        es_periferico=False,
    )
    db = _make_db_session(
        execute_results=[_FakeResult(rows=[(comp1, "Taller"), (comp2, "Taller")])]
    )

    resp = await tools_service.consultar_stock(db, tipo_pieza="RAM", ubicacion="Taller")

    assert resp.total == 2
    assert len(resp.resumen) == 1
    assert resp.resumen[0].cantidad == 2
    assert resp.resumen[0].laboratorio_nombre == "Taller"


async def test_consultar_historial_equipo_ok():
    equipo = Equipo(
        id=10,
        codigo_patrimonial="SBN-001",
        laboratorio_id=2,
        nombre_identificador="PC-01",
        tipo="Desktop",
        estado=EstadoEquipo.OPERATIVO,
        creado_en=datetime.now(timezone.utc),
    )
    comp = Componente(
        id=5,
        equipo_id=10,
        laboratorio_actual_id=2,
        tipo="CPU",
        descripcion="Intel i7",
        estado=EstadoComponente.INSTALADO,
    )
    mov = MovimientoHistorial(
        id=9,
        componente_id=5,
        equipo_origen_id=None,
        equipo_destino_id=10,
        lab_origen_id=1,
        lab_destino_id=2,
        tecnico_id=1,
        tipo_movimiento=TipoMovimiento.ASIGNACION,
        motivo="Instalación inicial",
    )
    db = _make_db_session(
        execute_results=[
            _FakeResult(rows=[(equipo, "Lab 1")]),
            _FakeResult(scalars_list=[comp]),
            _FakeResult(scalars_list=[mov]),
        ]
    )

    resp = await tools_service.consultar_historial_equipo(
        db, codigo_patrimonial="SBN-001"
    )

    assert resp.equipo.codigo_patrimonial == "SBN-001"
    assert len(resp.componentes_instalados) == 1
    assert len(resp.historial_movimientos) == 1


async def test_consultar_historial_equipo_not_found():
    db = _make_db_session(execute_results=[_FakeResult(rows=[])])

    with pytest.raises(ResourceNotFoundError):
        await tools_service.consultar_historial_equipo(
            db, codigo_patrimonial="NO-EXISTE"
        )


async def test_registrar_movimiento_asignacion_y_audit_log():
    tecnico = UsuarioTecnico(id=1, nombres="Ana", apellidos="Perez", activo=True)
    lab_dest = Laboratorio(id=2, nombre="Lab 1")
    comp = Componente(
        id=7,
        equipo_id=None,
        laboratorio_actual_id=1,
        tipo="RAM",
        descripcion="16GB DDR4",
        estado=EstadoComponente.EN_STOCK,
    )
    equipo_dest = Equipo(
        id=20,
        laboratorio_id=2,
        nombre_identificador="PC-20",
        estado=EstadoEquipo.OPERATIVO,
    )
    db = _make_db_session(
        get_map={(UsuarioTecnico, 1): tecnico, (Laboratorio, 2): lab_dest},
        execute_results=[
            _FakeResult(scalar=comp),
            _FakeResult(scalar=equipo_dest),
        ],
    )

    payload = RegistrarMovimientoRequest(
        id_pieza=7,
        origen_id=1,
        destino_id=2,
        equipo_destino_id=20,
        motivo="Upgrade de memoria",
        tecnico_id=1,
        origen_canal="TELEGRAM",
        actor_externo="tg_123",
    )
    resp = await tools_service.registrar_movimiento(db, payload, ip_address="127.0.0.1")

    assert resp.tipo_movimiento == TipoMovimiento.ASIGNACION
    assert resp.estado_componente == EstadoComponente.INSTALADO
    assert comp.laboratorio_actual_id == 2
    assert comp.equipo_id == 20
    assert any(isinstance(o, MovimientoHistorial) for o in db._added)
    audits = [o for o in db._added if isinstance(o, AuditLog)]
    assert len(audits) == 1
    assert audits[0].accion == "TRASLADO_COMPONENTE"
    assert audits[0].origen == "TELEGRAM"


async def test_registrar_movimiento_origen_mismatch_conflicto_concurrencia():
    tecnico = UsuarioTecnico(id=1, nombres="Ana", apellidos="Perez", activo=True)
    comp = Componente(
        id=7,
        equipo_id=None,
        laboratorio_actual_id=3,
        tipo="RAM",
        descripcion="16GB DDR4",
        estado=EstadoComponente.EN_STOCK,
    )
    db = _make_db_session(
        get_map={(UsuarioTecnico, 1): tecnico},
        execute_results=[_FakeResult(scalar=comp)],
    )

    payload = RegistrarMovimientoRequest(
        id_pieza=7,
        origen_id=1,
        destino_id=2,
        motivo="Intento concurrente",
        tecnico_id=1,
    )
    with pytest.raises(ConcurrencyConflictError):
        await tools_service.registrar_movimiento(db, payload)


async def test_registrar_movimiento_serialization_error_40001():
    tecnico = UsuarioTecnico(id=1, nombres="Ana", apellidos="Perez", activo=True)
    db = _make_db_session(get_map={(UsuarioTecnico, 1): tecnico})
    orig_err = SimpleNamespace(sqlstate="40001")
    db.execute = AsyncMock(side_effect=DBAPIError("SELECT", {}, orig_err))

    payload = RegistrarMovimientoRequest(
        id_pieza=7,
        origen_id=1,
        destino_id=2,
        motivo="Traslado en paralelo",
        tecnico_id=1,
    )
    with pytest.raises(ConcurrencyConflictError):
        await tools_service.registrar_movimiento(db, payload)


async def test_registrar_movimiento_validaciones_negocio():
    tecnico_inactivo = UsuarioTecnico(
        id=2, nombres="Inactivo", apellidos="User", activo=False
    )
    db_inactivo = _make_db_session(get_map={(UsuarioTecnico, 2): tecnico_inactivo})
    with pytest.raises(BusinessRuleViolationError):
        await tools_service.registrar_movimiento(
            db_inactivo,
            RegistrarMovimientoRequest(
                id_pieza=1, origen_id=1, destino_id=2, motivo="Test", tecnico_id=2
            ),
        )

    tecnico = UsuarioTecnico(id=1, nombres="Ana", apellidos="Perez", activo=True)
    comp_baja = Componente(
        id=8,
        laboratorio_actual_id=1,
        tipo="PSU",
        descripcion="500W",
        estado=EstadoComponente.DADO_DE_BAJA,
    )
    db_baja = _make_db_session(
        get_map={(UsuarioTecnico, 1): tecnico},
        execute_results=[_FakeResult(scalar=comp_baja)],
    )
    with pytest.raises(BusinessRuleViolationError):
        await tools_service.registrar_movimiento(
            db_baja,
            RegistrarMovimientoRequest(
                id_pieza=8, origen_id=1, destino_id=2, motivo="Test", tecnico_id=1
            ),
        )


async def test_registrar_baja_componente_bloquea_baja_definitiva():
    db = _make_db_session()
    payload = RegistrarBajaComponenteRequest(
        id_pieza=10,
        motivo_falla="Cortocircuito en placa",
        tecnico_id=1,
        baja_patrimonial_definitiva=True,
    )

    with pytest.raises(BusinessRuleViolationError) as exc_info:
        await tools_service.registrar_baja_componente(db, payload)

    assert exc_info.value.error_code == "BAJA_DEFINITIVA_BLOQUEADA"
    assert "0006-2021-EF/54.01" in exc_info.value.message


async def test_registrar_baja_componente_propuesta_ok():
    tecnico = UsuarioTecnico(id=1, nombres="Ana", apellidos="Perez", activo=True)
    comp = Componente(
        id=10,
        equipo_id=5,
        laboratorio_actual_id=2,
        tipo="PSU",
        descripcion="Fuente 500W",
        codigo_patrimonial="SBN-999",
        estado=EstadoComponente.INSTALADO,
    )
    db = _make_db_session(
        get_map={(UsuarioTecnico, 1): tecnico},
        execute_results=[_FakeResult(scalar=comp)],
    )

    payload = RegistrarBajaComponenteRequest(
        id_pieza=10,
        motivo_falla="No entrega voltaje 12V",
        tecnico_id=1,
    )
    resp = await tools_service.registrar_baja_componente(db, payload)

    assert resp.estado_componente == EstadoComponente.DEFECTUOSO
    assert resp.estado_patrimonial == "PENDIENTE_USGOM"
    assert comp.equipo_id is None
    assert comp.estado == EstadoComponente.DEFECTUOSO
    assert any(isinstance(o, Baja) for o in db._added)
    audits = [o for o in db._added if isinstance(o, AuditLog)]
    assert len(audits) == 1
    assert audits[0].accion == "PROPUESTA_BAJA_COMPONENTE"

