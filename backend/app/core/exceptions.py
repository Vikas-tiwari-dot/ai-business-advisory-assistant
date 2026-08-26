"""
App-wide exception types. Every one of these maps to the error envelope:

    {"error": {"code": ..., "message": ..., "fallback_used": bool}}

so the frontend never has to special-case error shapes per endpoint.
"""
from fastapi import Request
from fastapi.responses import JSONResponse


class RazorRecoverError(Exception):
    """Base class for all app-raised errors."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    fallback_used: bool = False

    def __init__(self, message: str, *, fallback_used: bool = False):
        super().__init__(message)
        self.message = message
        self.fallback_used = fallback_used


class ValidationFailedError(RazorRecoverError):
    code = "VALIDATION_FAILED"
    status_code = 422


class DuplicateEventError(RazorRecoverError):
    code = "DUPLICATE_EVENT"
    status_code = 409


class AISchemaInvalidError(RazorRecoverError):
    code = "AI_SCHEMA_INVALID"
    status_code = 200  # not a hard failure -- fallback handled it, request still succeeds


class AIUnavailableError(RazorRecoverError):
    code = "AI_UNAVAILABLE"
    status_code = 200


class PaymentGatewayError(RazorRecoverError):
    code = "PAYMENT_GATEWAY_ERROR"
    status_code = 502


class PolicyViolationError(RazorRecoverError):
    code = "POLICY_VIOLATION"
    status_code = 403


class DatabaseWriteError(RazorRecoverError):
    code = "DATABASE_WRITE_ERROR"
    status_code = 503


class NotFoundError(RazorRecoverError):
    code = "NOT_FOUND"
    status_code = 404


async def razorrecover_exception_handler(request: Request, exc: RazorRecoverError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "fallback_used": exc.fallback_used,
            }
        },
    )
