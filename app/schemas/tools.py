from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EstadoComponente, EstadoEquipo, TipoMovimiento


class StockComponenteItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    descripcion: str
    estado: EstadoComponente
    codigo_patrimonial: str | None = None
    numero_serie: str | None = None
    es_periferico: bool = False
    laboratorio_id: int
    laboratorio_nombre: str
    equipo_id: int | None = None


class StockResumenItem(BaseModel):
    tipo: str
    laboratorio_id: int
    laboratorio_nombre: str
    cantidad: int


class ConsultarStockResponse(BaseModel):
    total: int
    resumen: list[StockResumenItem]
    items: list[StockComponenteItem]


class ComponenteEquipoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    descripcion: str
    estado: EstadoComponente | None = None
    codigo_patrimonial: str | None = None
    numero_serie: str | None = None
    es_periferico: bool | None = None


class MovimientoEquipoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    componente_id: int
    equipo_origen_id: int | None = None
    equipo_destino_id: int | None = None
    lab_origen_id: int
    lab_destino_id: int
    tecnico_id: int
    tipo_movimiento: TipoMovimiento
    motivo: str
    fecha_movimiento: datetime | None = None


class EquipoDetalle(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo_patrimonial: str | None = None
    nombre_identificador: str
    tipo: str | None = None
    estado: EstadoEquipo | None = None
    laboratorio_id: int
    laboratorio_nombre: str
    creado_en: datetime | None = None


class ConsultarHistorialEquipoResponse(BaseModel):
    equipo: EquipoDetalle
    componentes_instalados: list[ComponenteEquipoItem]
    historial_movimientos: list[MovimientoEquipoItem]


class RegistrarMovimientoRequest(BaseModel):
    id_pieza: int = Field(..., gt=0, description="ID del componente a trasladar")
    origen_id: int = Field(..., gt=0, description="ID del laboratorio origen actual")
    destino_id: int = Field(..., gt=0, description="ID del laboratorio destino")
    motivo: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Justificación técnica del movimiento",
    )
    tecnico_id: int = Field(
        ..., gt=0, description="ID del técnico responsable en usuarios_tecnicos"
    )
    equipo_destino_id: int | None = Field(
        default=None,
        gt=0,
        description="ID opcional del equipo destino donde se instalará el componente",
    )
    origen_canal: str = Field(default="API", min_length=2, max_length=30)
    actor_externo: str | None = Field(default=None, max_length=100)


class RegistrarMovimientoResponse(BaseModel):
    movimiento_id: int
    componente_id: int
    tipo_movimiento: TipoMovimiento
    estado_componente: EstadoComponente
    lab_origen_id: int
    lab_destino_id: int
    equipo_origen_id: int | None = None
    equipo_destino_id: int | None = None
    fecha_movimiento: datetime | None = None
    audit_log_id: int


class RegistrarBajaComponenteRequest(BaseModel):
    id_pieza: int = Field(..., gt=0, description="ID del componente averiado")
    motivo_falla: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Diagnóstico o motivo técnico de falla",
    )
    tecnico_id: int = Field(..., gt=0, description="ID del técnico responsable")
    baja_patrimonial_definitiva: bool = Field(
        default=False,
        description=(
            "Bloqueado por código según Directiva N.° 0006-2021-EF/54.01; "
            "solo se permiten propuestas técnicas preliminares."
        ),
    )
    origen_canal: str = Field(default="API", min_length=2, max_length=30)
    actor_externo: str | None = Field(default=None, max_length=100)


class RegistrarBajaComponenteResponse(BaseModel):
    baja_id: int
    componente_id: int
    estado_componente: EstadoComponente
    estado_patrimonial: str
    fecha_baja: datetime | None = None
    audit_log_id: int
    mensaje: str

