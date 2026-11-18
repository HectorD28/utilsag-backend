from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.system import router as system_router
from app.api.v1.tools import router as tools_router

api_router = APIRouter()
api_router.include_router(tools_router)
api_router.include_router(agent_router)
api_router.include_router(system_router)


@api_router.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Verificación básica de disponibilidad del servicio."""
    return {"status": "ok"}
