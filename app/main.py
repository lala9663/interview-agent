"""FastAPI 앱. 실행: `uvicorn app.main:app --port 8000`"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.internal import health_router, router as internal_router
from app.container import Container
from app.live.ws import router as ws_router
from app.settings import Settings


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or Container(settings)
        logging.basicConfig(level=c.settings.server.log_level,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        app.state.container = c
        await c.startup()
        try:
            yield
        finally:
            await c.shutdown()

    app = FastAPI(title="interview-agent", version="0.1.0", lifespan=lifespan)
    cors = (container.settings if container else settings or Settings()).server.cors_origin_list
    app.add_middleware(CORSMiddleware, allow_origins=cors, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=True)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(ws_router)
    return app


app = create_app()
