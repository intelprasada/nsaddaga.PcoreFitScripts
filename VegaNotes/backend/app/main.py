from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .config import settings
from .db import ensure_data_dirs, init_db, session_scope
from .indexer import reindex_all, watch_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("veganotes")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    init_db()
    with session_scope() as s:
        n = reindex_all(s)
        log.info("Initial reindex: %d files", n)
    task = asyncio.create_task(watch_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="VegaNotes", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


app.include_router(api_router, prefix="/api")


_sockets: set[WebSocket] = set()


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    _sockets.add(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        _sockets.discard(socket)


# Serve the built React frontend in single-pod mode (after all /api routes).
if settings.serve_static and Path(settings.static_dir).exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
