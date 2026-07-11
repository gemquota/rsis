"""Web dashboard — FastAPI + HTMX for RSIS monitoring & control."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn

from rsis.config import RSISConfig
from rsis.memory.knowledge_graph import KnowledgeGraph
from rsis.memory.vector_store import VectorStore
from rsis.telemetry.collector import TelemetryCollector
from rsis.telemetry.extrapolator import Extrapolator

app = FastAPI(title="RSIS Dashboard")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Runtime state (set by create_app)
config: Optional[RSISConfig] = None
kg: Optional[KnowledgeGraph] = None
vector_store: Optional[VectorStore] = None
telemetry: Optional[TelemetryCollector] = None
extrapolator: Optional[Extrapolator] = None


def create_app(
    cfg: RSISConfig,
    kg_instance: KnowledgeGraph,
    vs: VectorStore,
    tel: TelemetryCollector,
    ext: Extrapolator,
) -> FastAPI:
    global config, kg, vector_store, telemetry, extrapolator
    config = cfg
    kg = kg_instance
    vector_store = vs
    telemetry = tel
    extrapolator = ext
    return app


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = config
    trends = extrapolator.analyze_trends() if extrapolator else {}
    velocity = extrapolator.improvement_velocity() if extrapolator else {}
    recent = telemetry.recent_events(20) if telemetry else []

    return templates.TemplateResponse("index.html", {
        "request": request,
        "config": cfg,
        "trends": trends,
        "velocity": velocity,
        "recent_events": recent,
        "kg_nodes": kg._graph.number_of_nodes() if kg else 0,
        "kg_edges": kg._graph.number_of_edges() if kg else 0,
        "vector_count": vector_store.count() if vector_store else 0,
        "uptime_s": int(time.time() - _start_time) if _start_time else 0,
    })


@app.get("/api/trends")
async def api_trends():
    if not extrapolator:
        return {"error": "extrapolator not initialized"}
    return extrapolator.analyze_trends()


@app.get("/api/velocity")
async def api_velocity():
    if not extrapolator:
        return {"error": "extrapolator not initialized"}
    return extrapolator.improvement_velocity()


@app.get("/api/memory")
async def api_memory():
    return {
        "kg_nodes": kg._graph.number_of_nodes() if kg else 0,
        "kg_edges": kg._graph.number_of_edges() if kg else 0,
        "vector_count": vector_store.count() if vector_store else 0,
    }


@app.get("/api/telemetry/{event_type}")
async def api_telemetry(event_type: str, n: int = 50):
    if not telemetry:
        return {"error": "telemetry not initialized"}
    return {"events": telemetry.recent_events(n, event_type=event_type)}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "kg_nodes": kg._graph.number_of_nodes() if kg else 0,
        "vectors": vector_store.count() if vector_store else 0,
        "timestamp": time.time(),
    }


_start_time = time.time()


def serve(cfg: RSISConfig, port: int = 8766) -> None:
    """Start the dashboard server."""
    uvicorn.run(app, host=cfg.dashboard.host, port=port, log_level=cfg.dashboard.log_level)
