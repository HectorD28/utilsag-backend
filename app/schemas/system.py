from datetime import datetime

from pydantic import BaseModel, Field


class KillSwitchToggleRequest(BaseModel):
    active: bool = Field(
        default=True,
        description="True para activar el Kill Switch (modo Inactivo); False para reanudar operaciones.",
    )
    motivo: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Motivo de la detención de emergencia o reanudación",
    )
    usuario_id: int | None = Field(
        default=None,
        gt=0,
        description="ID del administrador en usuarios_tecnicos (opcional)",
    )
    actor_externo: str | None = Field(
        default=None,
        max_length=100,
        description="Identificador del administrador (ej. usuario web o Telegram ID)",
    )
    origen: str = Field(default="API", min_length=2, max_length=30)


class KillSwitchStatusResponse(BaseModel):
    active: bool
    estado_agente: str
    motivo: str | None = None
    usuario_id: int | None = None
    actor_externo: str | None = None
    actualizado_en: datetime | None = None
    audit_log_id: int | None = None


class TelegramDeniedAuditRequest(BaseModel):
    telegram_user_id: int
    username: str | None = Field(default=None, max_length=100)
    chat_id: int | None = None
    ip_address: str | None = Field(default=None, max_length=45)
    detalle: str | None = Field(default=None, max_length=500)


class TelegramDeniedAuditResponse(BaseModel):
    audit_log_id: int
    registrado: bool = True

