"""健康检查、语义包列表、工作台统计。"""

import subprocess
import time

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import config
from backend.agent.sandbox import resolve_docker
from backend.semantic import list_packs
from backend.db import get_db
from backend.models import Dashboard, DataSource, Insight, Run, SceneAgent, Session as DbSession, Skill, TokenUsage

router = APIRouter()

_sandbox_cache: dict = {"ok": False, "checked_at": 0.0, "ttl": 60.0}


def sandbox_available() -> bool:
    now = time.time()
    if now - _sandbox_cache["checked_at"] < _sandbox_cache["ttl"]:
        return _sandbox_cache["ok"]
    docker = resolve_docker()
    ok = False
    if docker:
        try:
            proc = subprocess.run([docker, "info"], capture_output=True, timeout=10)
            ok = proc.returncode == 0
        except Exception:
            ok = False
    _sandbox_cache.update(ok=ok, checked_at=now)
    return ok


@router.get("/health")
def health():
    return {
        "status": "ok",
        "sandbox": sandbox_available(),
        "model": config.MODEL_NAME if config.OPENAI_API_KEY else "",
        "llm_configured": bool(config.OPENAI_API_KEY),
        "packs": list_packs(),
    }


@router.get("/semantic/packs")
def packs():
    return list_packs()


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "sessions": db.query(DbSession).count(),
        "runs": db.query(Run).count(),
        "runs_ok": db.query(Run).filter(Run.ok == True).count(),  # noqa: E712
        "data_sources": db.query(DataSource).count(),
        "skills": db.query(Skill).filter(Skill.enabled == True).count(),  # noqa: E712
        "agents": db.query(SceneAgent).filter(SceneAgent.enabled == True).count(),  # noqa: E712
        "insights_new": db.query(Insight).filter(Insight.status == "new").count(),
        "dashboards": db.query(Dashboard).count(),
        "token_total_input": db.query(func.sum(TokenUsage.input_tokens)).scalar() or 0,
        "token_total_output": db.query(func.sum(TokenUsage.output_tokens)).scalar() or 0,
        "token_total_cost": round(db.query(func.sum(TokenUsage.cost_usd)).scalar() or 0, 6),
        "token_runs": db.query(func.count(TokenUsage.id)).scalar() or 0,
    }
