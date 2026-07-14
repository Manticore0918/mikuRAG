from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, auth, conversations, documents, knowledge_bases
from app.config import get_settings
from app.database import close_database
from app.health import readiness
from app.rate_limit import login_rate_limiter


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await login_rate_limiter.close()
    await close_database()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="mikuRAG API",
        version="0.1.0",
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        allow_origins=settings.cors_origins,
    )
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(knowledge_bases.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")

    @app.get("/api/v1/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health/ready", tags=["health"])
    async def ready() -> JSONResponse:
        is_ready, dependencies = await readiness()
        content: dict[str, Any] = {
            "status": "ok" if is_ready else "error",
            "dependencies": dependencies,
        }
        return JSONResponse(status_code=200 if is_ready else 503, content=content)

    return app


app = create_app()
