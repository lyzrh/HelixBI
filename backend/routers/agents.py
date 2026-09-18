"""场景 Agent 路由：CRUD。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.semantic import list_packs
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import DataSource, SceneAgent
from backend.schemas import AgentCreate, AgentPatch

router = APIRouter(prefix="/agents",
                   dependencies=[Depends(get_current_context)])


@router.get("")
def list_agents(db: Session = Depends(get_db)):
    return [a.to_dict() for a in db.query(SceneAgent).order_by(SceneAgent.id).all()]


@router.post("")
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    valid = {p["id"] for p in list_packs()}
    if body.pack_id not in valid:
        raise HTTPException(400, f"语义包不存在，可选: {sorted(valid)}")
    for ds_id in body.data_source_ids:
        if not db.get(DataSource, ds_id):
            raise HTTPException(400, f"数据源 {ds_id} 不存在")
    obj = SceneAgent(
        name=body.name, description=body.description, pack_id=body.pack_id,
        data_source_ids=json.dumps(body.data_source_ids, ensure_ascii=False),
        intro=body.intro,
        recommended_questions=json.dumps(body.recommended_questions, ensure_ascii=False),
        icon=body.icon, color=body.color,
    )
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.get("/{aid}")
def get_agent(aid: int, db: Session = Depends(get_db)):
    a = db.get(SceneAgent, aid)
    if not a:
        raise HTTPException(404, "场景 Agent 不存在")
    return a.to_dict()


@router.patch("/{aid}")
def patch_agent(aid: int, body: AgentPatch, db: Session = Depends(get_db)):
    a = db.get(SceneAgent, aid)
    if not a:
        raise HTTPException(404, "场景 Agent 不存在")
    if body.pack_id is not None:
        valid = {p["id"] for p in list_packs()}
        if body.pack_id not in valid:
            raise HTTPException(400, f"语义包不存在，可选: {sorted(valid)}")
        a.pack_id = body.pack_id
    if body.data_source_ids is not None:
        a.data_source_ids = json.dumps(body.data_source_ids, ensure_ascii=False)
    if body.name is not None:
        a.name = body.name
    if body.description is not None:
        a.description = body.description
    if body.intro is not None:
        a.intro = body.intro
    if body.recommended_questions is not None:
        a.recommended_questions = json.dumps(body.recommended_questions, ensure_ascii=False)
    if body.icon is not None:
        a.icon = body.icon
    if body.color is not None:
        a.color = body.color
    if body.enabled is not None:
        a.enabled = body.enabled
    db.commit()
    return a.to_dict()


@router.delete("/{aid}")
def delete_agent(aid: int, db: Session = Depends(get_db)):
    a = db.get(SceneAgent, aid)
    if not a:
        raise HTTPException(404, "场景 Agent 不存在")
    if a.builtin:
        raise HTTPException(400, "内置场景 Agent 不可删除，可停用")
    db.delete(a)
    db.commit()
    return {"ok": True}
