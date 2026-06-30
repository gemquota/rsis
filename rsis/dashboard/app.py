"""FastAPI + HTMX dashboard for RSIS telemetry and reporting."""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rsis.config import CONFIG
from rsis.extrapolation import TelemetryExtrapolator
from rsis.memory import MemoryManager

app = FastAPI(title="RSIS Dashboard")

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _get_data():
    """Get fresh data for dashboard views."""
    memory = MemoryManager(CONFIG.workspace_dir)
    extrap = TelemetryExtrapolator()
    return memory, extrap


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    memory, extrap = _get_data()
    velocity = extrap.generate_velocity_report()
    trends = extrap.detect_regression_trends()
    sessions = extrap.get_sessions()

    # KG stats
    kg_nodes = memory.kg.node_count
    kg_edges = memory.kg.edge_count
    vec_docs = len(memory.vectors._documents)
    strategies = memory.kg.get_strategies()
    recent = memory.kg.get_insights(limit=10)

    # Optimal budget
    optimal_iters = extrap.predict_optimal_iterations()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "velocity": velocity,
        "trends": trends,
        "sessions": sessions[-20:],  # last 20 sessions
        "kg_nodes": kg_nodes,
        "kg_edges": kg_edges,
        "vec_docs": vec_docs,
        "strategies": strategies,
        "recent_insights": recent,
        "optimal_iters": optimal_iters,
    })


@app.get("/api/status")
async def api_status():
    memory, extrap = _get_data()
    return {
        "kg_nodes": memory.kg.node_count,
        "kg_edges": memory.kg.edge_count,
        "vector_docs": len(memory.vectors._documents),
        "strategies": len(memory.kg.get_strategies()),
        "sessions": len(extrap.get_sessions()),
        "optimal_l2_budget": extrap.predict_optimal_iterations(),
    }


@app.get("/api/trends")
async def api_trends():
    _, extrap = _get_data()
    return {"trends": extrap.detect_regression_trends()}


@app.get("/api/velocity")
async def api_velocity():
    _, extrap = _get_data()
    return extrap.generate_velocity_report()


@app.get("/api/search", response_class=HTMLResponse)
async def api_search(request: Request, q: str = ""):
    memory, _ = _get_data()
    results = memory.get_relevant_patterns(q) if q else []
    return templates.TemplateResponse("_search_results.html", {
        "request": request,
        "results": results,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
