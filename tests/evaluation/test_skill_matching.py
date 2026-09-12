"""Skill 匹配层评估：路由到的是不是「同一条已验证路径」。

注意指标选择：每条查询只对应一条正确路径，precision@k 会被 k 结构性截断（≤1/k），
所以这里看 Top1 准确率与 Recall@k，而不是 precision。
"""

import json

from backend.evaluation import runner
from backend.skills import engine as skill_engine


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add(session, question: str, pack_id: str, tags: list[str]):
    from backend.models import Skill

    skill = Skill(name=question[:20], question=question, pack_id=pack_id,
                  spec="{}", code="", columns_json="[]",
                  tags=json.dumps(tags, ensure_ascii=False))
    session.add(skill)
    session.flush()
    return skill


def test_top1_accuracy_above_threshold(report):
    assert report["skills"]["top1_accuracy"] >= 0.70


def test_recall_at_k_above_threshold(report):
    assert report["skills"]["recall"] >= 0.80


def test_hit_rate_is_full(report):
    """只要库里有同语义包的技能，就应该至少召回一个（不能空手而归）。"""
    assert report["skills"]["hit_rate"] == 1.0


def test_semantic_signal_improves_routing(monkeypatch, every_case):
    """A/B：关掉「分析类型一致性」信号后 Top1 必须变差。

    这条测试锁住的是一个设计决策——仅靠词面 2-gram，无法区分
    「销售额排序」与「销售额环比」这类同指标不同口径的问题。
    """
    import backend.semantic as semantic

    with_signal = runner.eval_skills(every_case)
    real_resolve = semantic.resolve
    monkeypatch.setattr(semantic, "resolve",
                        lambda question, pack_id: {"analysis_type": "unknown"})
    try:
        without_signal = runner.eval_skills(every_case)
    finally:
        monkeypatch.setattr(semantic, "resolve", real_resolve)

    assert with_signal["top1_accuracy"] > without_signal["top1_accuracy"], (
        f"语义信号未生效：with={with_signal['top1_accuracy']:.3f} "
        f"without={without_signal['top1_accuracy']:.3f}")


def test_pack_boost_prefers_same_pack():
    session = _session()
    same = _add(session, "各品类销售额", "retail_sales", ["breakdown"])
    other = _add(session, "各品类销售额", "manufacturing_production", ["breakdown"])
    session.commit()
    top = skill_engine.match_skills(session, "各品类销售额是多少", pack_id="retail_sales", limit=1)
    assert top and top[0].id == same.id
    session.close()


def test_disabled_skill_is_not_matched():
    session = _session()
    skill = _add(session, "各品类销售额", "retail_sales", ["breakdown"])
    skill.enabled = False
    session.commit()
    assert skill_engine.match_skills(session, "各品类销售额是多少", limit=2) == []
    session.close()
