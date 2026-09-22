"""Skill 路由：CRUD + 从运行沉淀 + SSE 运行。

安全边界（Security Hardening V1）：
- 列表已有作用域过滤，**按 id 读取 / 修改 / 删除原先没有**——猜到 id 就能读别人
  工作区（乃至别人的 user 作用域）Skill 的代码与口径；现在统一走 `_visible_skill`，
  越权一律 404（与产物访问同一约定，不暴露存在性）；
- 从运行沉淀（from-run）先校验该 Run 归属当前工作区，避免把别人的分析结果沉淀成
  自己的资产；
- 手动运行 Skill 保持既有 403 + 工作区门禁（两处一致，纵深防御）。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import Run, Session as DbSession, Skill
from backend.routers.analysis import _semaphore, sse_stream
from backend.schemas import SkillCreate, SkillFromRun, SkillPatch, SkillRunBody
from backend.skills import engine as skill_engine
from backend.skills import retrieval

router = APIRouter(prefix="/skills",
                   dependencies=[Depends(get_current_context),
                                 Depends(require_permission("skill:read"))])


def _visible_skill(db: Session, skid: int, ctx: UserContext) -> Skill:
    """按 id 取 Skill 并校验可见性 → 不可见一律 404。

    规则与检索层共用同一个实现（`skills/retrieval.skill_visible`）：
    global 全库可见；workspace 限本工作区（历史无归属视为全局）；**user 只限本人**。
    """
    s = db.get(Skill, skid)
    if not retrieval.skill_visible(s, ctx.workspace_id, ctx.user_id):
        raise HTTPException(404, "Skill 不存在")
    return s


@router.get("")
def list_skills(db: Session = Depends(get_db),
                ctx: UserContext = Depends(get_current_context)):
    # 作用域过滤与检索层共用一份规则（user 作用域只看本人；历史无归属视为全局）
    rows = (db.query(Skill)
            .filter(retrieval.skill_scope_filter(ctx.workspace_id, ctx.user_id))
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
    # 只能从本工作区的运行沉淀资产（历史匿名运行 workspace_id 为空，保持兼容）
    run = db.get(Run, body.run_id)
    session = db.get(DbSession, run.session_id) if run and run.session_id else None
    if not run or not session:
        raise HTTPException(404, "运行记录不存在")
    if session.workspace_id is not None and session.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "运行记录不存在")
    try:
        return skill_engine.capture_from_run(db, body.run_id, body.name,
                                             body.description, body.tags,
                                             workspace_id=ctx.workspace_id,
                                             user_id=ctx.user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{skid}")
def get_skill(skid: int, db: Session = Depends(get_db),
              ctx: UserContext = Depends(get_current_context)):
    return _visible_skill(db, skid, ctx).to_dict()


@router.patch("/{skid}")
def patch_skill(skid: int, body: SkillPatch, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("skill:write"))):
    s = _visible_skill(db, skid, ctx)
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
                 ctx: UserContext = Depends(require_permission("skill:write"))):
    s = _visible_skill(db, skid, ctx)
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
    # 作用域门：跨工作区的 Skill 不允许运行（与检索层同一套可见性规则，保持 403 语义）
    if not retrieval.skill_visible(s, _ctx.workspace_id, _ctx.user_id):
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
