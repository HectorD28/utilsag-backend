from app.schemas.agent import (
    AgentQueryRequest,
    AgentQueryResponse,
    ToolCallExecution,
    VisionOCRRequest,
    VisionOCRResponse,
)
from app.schemas.system import (
    KillSwitchStatusResponse,
    KillSwitchToggleRequest,
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

__all__ = [
    "AgentQueryRequest",
    "AgentQueryResponse",
    "ComponenteEquipoItem",
    "ConsultarHistorialEquipoResponse",
    "ConsultarStockResponse",
    "EquipoDetalle",
    "KillSwitchStatusResponse",
    "KillSwitchToggleRequest",
    "MovimientoEquipoItem",
    "RegistrarBajaComponenteRequest",
    "RegistrarBajaComponenteResponse",
    "RegistrarMovimientoRequest",
    "RegistrarMovimientoResponse",
    "StockComponenteItem",
    "StockResumenItem",
    "ToolCallExecution",
    "VisionOCRRequest",
    "VisionOCRResponse",
]
