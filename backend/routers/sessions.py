"""会话 CRUD + 历史消息。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Message, Run, SceneAgent, Session as DbSession
from backend.schemas import SessionCreate, SessionPatch

router = APIRouter(prefix="/sessions")


@router.post("")
def create_session(body: SessionCreate, db: Session = Depends(get_db)):
    agent = None
    if body.agent_id:
        agent = db.get(SceneAgent, body.agent_id)
        if not agent:
            raise HTTPException(404, "场景 Agent 不存在")
    title = body.title or (agent.name if agent else "新会话")
    obj = DbSession(title=title, agent_id=body.agent_id)
    db.add(obj)
    db.flush()
    result = obj.to_dict()
    if agent:
        result["agent"] = agent.to_dict()
    db.commit()
    return result


@router.get("")
def list_sessions(db: Session = Depends(get_db)):
    sessions = db.query(DbSession).order_by(DbSession.updated_at.desc()).all()
    out = []
    for s in sessions:
        d = s.to_dict()
        d["message_count"] = db.query(Message).filter(Message.session_id == s.id).count()
        if s.agent_id:
            agent = db.get(SceneAgent, s.agent_id)
            if agent:
                d["agent"] = agent.to_dict()
        out.append(d)
    return out


@router.get("/{sid}")
def get_session(sid: int, db: Session = Depends(get_db)):
    s = db.get(DbSession, sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    messages = db.query(Message).filter(Message.session_id == sid).order_by(Message.id).all()
    d = s.to_dict()
    d["messages"] = [m.to_dict() for m in messages]
    if s.agent_id:
        agent = db.get(SceneAgent, s.agent_id)
        if agent:
            d["agent"] = agent.to_dict()
    return d


@router.patch("/{sid}")
def patch_session(sid: int, body: SessionPatch, db: Session = Depends(get_db)):
    s = db.get(DbSession, sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    if body.title is not None:
        s.title = body.title
    if body.agent_id is not None:
        s.agent_id = body.agent_id
    db.commit()
    return s.to_dict()


@router.delete("/{sid}")
def delete_session(sid: int, db: Session = Depends(get_db)):
    s = db.get(DbSession, sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    db.query(Run).filter(Run.session_id == sid).delete()
    db.query(Message).filter(Message.session_id == sid).delete()
    db.delete(s)
    db.commit()
    return {"ok": True}
