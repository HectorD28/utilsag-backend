from fastapi import HTTPException, status


class UtilSAGError(Exception):
    """Excepción base de dominio para UtilSAG-AI."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = "UTILSAG_ERROR",
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


class ResourceNotFoundError(UtilSAGError):
    """Recurso solicitado no existe en la base de datos."""

    def __init__(self, message: str, error_code: str = "RESOURCE_NOT_FOUND"):
        super().__init__(
            message, status_code=status.HTTP_404_NOT_FOUND, error_code=error_code
        )


class BusinessRuleViolationError(UtilSAGError):
    """Violación de regla de negocio o normativa patrimonial."""

    def __init__(self, message: str, error_code: str = "BUSINESS_RULE_VIOLATION"):
        super().__init__(
            message,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error_code=error_code,
        )


class ConcurrencyConflictError(UtilSAGError):
    """Conflicto de serialización o concurrencia en transacción SERIALIZABLE."""

    def __init__(
        self,
        message: str = "Conflicto de concurrencia; reintente la operación.",
        error_code: str = "CONCURRENCY_CONFLICT",
    ):
        super().__init__(
            message, status_code=status.HTTP_409_CONFLICT, error_code=error_code
        )


def to_http_exception(exc: UtilSAGError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error_code": exc.error_code, "detail": exc.message},
    )
