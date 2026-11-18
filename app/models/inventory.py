from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EstadoComponente, EstadoEquipo, TipoMovimiento


class Laboratorio(Base):
    __tablename__ = "laboratorios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    es_almacen: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    creado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )

    equipos: Mapped[list["Equipo"]] = relationship(back_populates="laboratorio")
    componentes: Mapped[list["Componente"]] = relationship(
        back_populates="laboratorio_actual"
    )


class Equipo(Base):
    __tablename__ = "equipos"
    __table_args__ = (Index("idx_equipos_lab", "laboratorio_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo_patrimonial: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True
    )
    laboratorio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("laboratorios.id", ondelete="RESTRICT"), nullable=False
    )
    nombre_identificador: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estado: Mapped[EstadoEquipo | None] = mapped_column(
        SAEnum(EstadoEquipo, name="estado_equipo_enum", create_type=False),
        nullable=True,
        server_default=text("'OPERATIVO'::estado_equipo_enum"),
    )
    creado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )

    laboratorio: Mapped["Laboratorio"] = relationship(back_populates="equipos")
    componentes: Mapped[list["Componente"]] = relationship(back_populates="equipo")


class Componente(Base):
    __tablename__ = "componentes"
    __table_args__ = (
        Index("idx_componentes_equipo", "equipo_id"),
        Index("idx_componentes_patrimonial", "codigo_patrimonial"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo_origen: Mapped[str | None] = mapped_column(String(50), nullable=True)
    equipo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("equipos.id", ondelete="SET NULL"), nullable=True
    )
    laboratorio_actual_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("laboratorios.id"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(255), nullable=False)
    codigo_patrimonial: Mapped[str | None] = mapped_column(
        String(50), nullable=True, unique=True
    )
    estado: Mapped[EstadoComponente | None] = mapped_column(
        SAEnum(EstadoComponente, name="estado_componente_enum", create_type=False),
        nullable=True,
        server_default=text("'EN_STOCK'::estado_componente_enum"),
    )
    es_periferico: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    creado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )
    numero_serie: Mapped[str | None] = mapped_column(String(100), nullable=True)

    equipo: Mapped["Equipo | None"] = relationship(back_populates="componentes")
    laboratorio_actual: Mapped["Laboratorio"] = relationship(
        back_populates="componentes"
    )


class UsuarioTecnico(Base):
    __tablename__ = "usuarios_tecnicos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    nombres: Mapped[str] = mapped_column(String(100), nullable=False)
    apellidos: Mapped[str] = mapped_column(String(100), nullable=False)
    activo: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("true")
    )


class MovimientoHistorial(Base):
    __tablename__ = "movimientos_historial"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    componente_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("componentes.id"), nullable=False
    )
    equipo_origen_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("equipos.id"), nullable=True
    )
    equipo_destino_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("equipos.id"), nullable=True
    )
    lab_origen_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("laboratorios.id"), nullable=False
    )
    lab_destino_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("laboratorios.id"), nullable=False
    )
    tecnico_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("usuarios_tecnicos.id"), nullable=False
    )
    tipo_movimiento: Mapped[TipoMovimiento] = mapped_column(
        SAEnum(TipoMovimiento, name="tipo_movimiento_enum", create_type=False),
        nullable=False,
    )
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_movimiento: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )


class Baja(Base):
    __tablename__ = "bajas"
    __table_args__ = (
        CheckConstraint(
            "(componente_id IS NOT NULL AND equipo_id IS NULL) OR "
            "(componente_id IS NULL AND equipo_id IS NOT NULL)",
            name="chk_baja_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    componente_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("componentes.id"), nullable=True
    )
    equipo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("equipos.id"), nullable=True
    )
    tecnico_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("usuarios_tecnicos.id"), nullable=False
    )
    motivo_falla: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_baja: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_accion", "accion", "tabla_afectada"),
        Index("idx_audit_logs_fecha", text("creado_en DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("usuarios_tecnicos.id"), nullable=True
    )
    actor_externo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origen: Mapped[str] = mapped_column(String(30), nullable=False)
    accion: Mapped[str] = mapped_column(String(50), nullable=False)
    tabla_afectada: Mapped[str | None] = mapped_column(String(50), nullable=True)
    registro_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    datos_anteriores: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    datos_nuevos: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
