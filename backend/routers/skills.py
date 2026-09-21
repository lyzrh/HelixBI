"""Skill 路由：CRUD + 从运行沉淀 + SSE 运行。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import Skill
from backend.routers.analysis import _semaphore, sse_stream
from backend.schemas import SkillCreate, SkillFromRun, SkillPatch, SkillRunBody
from backend.skills import engine as skill_engine

router = APIRouter(prefix="/skills",
                   dependencies=[Depends(get_current_context),
                                 Depends(require_permission("skill:read"))])


@router.get("")
def list_skills(db: Session = Depends(get_db),
                ctx: UserContext = Depends(get_current_context)):
    # 作用域：global + 本工作区 + 本人（历史无归属数据视为全局）
    rows = (db.query(Skill)
            .filter((Skill.scope == "global")
                    | (Skill.workspace_id == ctx.workspace_id)
                    | (Skill.workspace_id.is_(None))
                    | ((Skill.scope == "user") & (Skill.user_id == ctx.user_id)))
            .order_by(Skill.id).all())
    return [s.to_dict() for s in rows]


@router.post("")
def create_skill(body: SkillCreate, db: Session = Depends(get_db),
                 ctx: UserContext = Depends(require_permission("skill:write"))):
    if not body.code.strip():
        raise HTTPException(400, "Skill 必须包含可执行代码")
    obj = Skill(name=body.name, description=body.description, pack_id=body.pack_id,
                question=body.question, code=body.code, tags=json.dumps(
                    body.tags, ensure_ascii=False),
                scope="workspace", workspace_id=ctx.workspace_id, user_id=ctx.user_id)
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.post("/from-run")
def from_run(body: SkillFromRun, db: Session = Depends(get_db),
             ctx: UserContext = Depends(require_permission("skill:write"))):
    try:
        return skill_engine.capture_from_run(db, body.run_id, body.name,
                                             body.description, body.tags,
                                             workspace_id=ctx.workspace_id,
                                             user_id=ctx.user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{skid}")
def get_skill(skid: int, db: Session = Depends(get_db)):
    s = db.get(Skill, skid)
    if not s:
        raise HTTPException(404, "Skill 不存在")
    return s.to_dict()


@router.patch("/{skid}")
def patch_skill(skid: int, body: SkillPatch, db: Session = Depends(get_db),
                _ctx: UserContext = Depends(require_permission("skill:write"))):
    s = db.get(Skill, skid)
    if not s:
        raise HTTPException(404, "Skill 不存在")
    if body.name is not None:
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    if body.enabled is not None:
        s.enabled = body.enabled
    if body.tags is not None:
        s.tags = json.dumps(body.tags, ensure_ascii=False)
    db.commit()
    return s.to_dict()


@router.delete("/{skid}")
def delete_skill(skid: int, db: Session = Depends(get_db),
                 _ctx: UserContext = Depends(require_permission("skill:write"))):
    s = db.get(Skill, skid)
    if not s:
        raise HTTPException(404, "Skill 不存在")
    if s.builtin:
        raise HTTPException(400, "内置 Skill 不可删除，可停用")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/{skid}/run")
async def run_skill(skid: int, body: SkillRunBody, db: Session = Depends(get_db),
                    _ctx: UserContext = Depends(require_permission("analysis:execute"))):
    """手动运行 Skill（用户显式指定，不再走准入筛选，但结构守卫仍然生效）。"""
    s = db.get(Skill, skid)
    if not s:
        raise HTTPException(404, "Skill 不存在")
    if not s.enabled:
        raise HTTPException(400, "Skill 已停用")
    # 作用域门：跨工作区的 Skill 不允许运行（与检索层同一套可见性规则）
    if not (_ctx.workspace_id is None or s.scope == "global"
            or s.workspace_id in (_ctx.workspace_id, None)):
        raise HTTPException(403, "Skill 不属于当前工作区")

    if _semaphore.locked():
        raise HTTPException(429, "已有分析任务在执行中，请稍候再试")
    await _semaphore.acquire()

    def work(on_event):
        return skill_engine.run_skill(
            skid, body.session_id, body.data_source_ids, on_event,
            workspace_id=_ctx.workspace_id,
        )

    return await sse_stream(work)
