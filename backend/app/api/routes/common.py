from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.errors import NotFoundError, NotAuthenticatedError


def not_implemented(resource: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "status": "not_implemented",
            "resource": resource,
            "message": "This endpoint is reserved for a later phase.",
        },
    )


def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


def not_authenticated_handler(_: Request, exc: NotAuthenticatedError) -> JSONResponse:
    """401 for service-layer identity failures (fail-closed identity resolution)."""
    return JSONResponse(
        status_code=401, content={"detail": str(exc) or "Authentication required."}
    )