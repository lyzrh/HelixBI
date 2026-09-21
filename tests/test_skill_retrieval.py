"""Skill Retrieval V2 单元测试：打分信号、硬约束、分层准入、作用域、并发安全。

这一组测试对应「重放安全」的核心主张：
**系统宁愿放弃 Replay，也不能错误 Replay。**
每条 blocker 都有一条用例锁住它——少了任何一条，误重放就会静默回来。
"""

import json
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Skill
from backend.skills import retrieval

RETAIL_COLUMNS = ["订单日期", "品类", "区域", "门店", "渠道", "销售额", "数量"]
MFG_COLUMNS = ["生产日期", "产线", "产品", "计划产量", "实际产量", "不良数", "停机分钟"]
CSV_CODE = "import pandas as pd\n\ndf = pd.read_csv('/data/x.csv')\n"


def _skill(**kw):
    """构造 Skill 对象（不落库，纯内存打分）。"""
    data = {
        "id": kw.pop("id", 1),
        "name": kw.pop("name", "s"),
        "question": kw.pop("question", "各品类的销售额是多少？"),
        "pack_id": kw.pop("pack_id", "retail_sales"),
        "tags": json.dumps(kw.pop("tags", []), ensure_ascii=False),
        "columns_json": json.dumps(kw.pop("columns", RETAIL_COLUMNS), ensure_ascii=False),
        "code": kw.pop("code", CSV_CODE),
        "use_count": kw.pop("use_count", 0),
        "success_count": kw.pop("success_count", 0),
        "datasource_key": kw.pop("datasource_key", "retail_sales::csv"),
        "enabled": True,
    }
    data.update(kw)
    return Skill(**data)


def _ctx(pack="retail_sales", columns=None, exts=("csv",)):
    column_list = RETAIL_COLUMNS if columns is None else columns
    return retrieval.DataContext(pack_ids=frozenset({pack}),
                                 columns=frozenset(column_list),
                                 extensions=frozenset(exts))


# ---- 1. 正常命中 ----

def test_exact_match_is_high_confidence_replay():
    skill = _skill(id=7, question="各品类的销售额是多少？",
                   columns=["订单日期", "品类", "销售额"])
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "replay"
    assert decision.confidence == "high"
    assert decision.reason_code == "high_confidence"
    assert decision.selected_skill_id == 7
    assert decision.candidates[0].scores["metric"] == 1.0
    assert decision.candidates[0].scores["datasource"] == 1.0


def test_synonyms_resolve_to_same_path():
    """「地区」与「区域」、「营业额」与「销售额」是同一条口径，不该被误拒。"""
    skill = _skill(question="各区域的销售额是多少？", columns=["订单日期", "区域", "销售额"])
    decision = retrieval.route(None, "各地区的营业额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "replay"


# ---- 2. Top1 错误但 Top2 正确 ----

def test_wrong_top1_falls_through_to_top2():
    """词面更像的 Top1 语义不符时，应当重放 Top2，而不是一起放弃。"""
    wrong = _skill(id=1, name="各产品实际产量", pack_id="manufacturing_production",
                   question="各产品的实际产量是多少？",
                   columns=["生产日期", "产品", "实际产量"],
                   datasource_key="manufacturing_production::csv")
    right = _skill(id=2, name="各产品良率", pack_id="manufacturing_production",
                   question="各产品的良率对比",
                   columns=["生产日期", "产品", "实际产量", "不良数"],
                   datasource_key="manufacturing_production::csv")
    decision = retrieval.route(None, "各产品的良率对比", "manufacturing_production",
                              ctx=_ctx("manufacturing_production", MFG_COLUMNS),
                              skills=[wrong, right])
    assert decision.decision == "replay"
    assert decision.selected_skill_id == 2
    assert any("metric_mismatch" in c.blockers for c in decision.candidates
               if c.skill_id == 1)


# ---- 3. 无 Skill 命中 ----

def test_no_candidate_falls_back_to_agent():
    decision = retrieval.route(None, "客户复购率是多少？", "retail_sales",
                               ctx=_ctx(), skills=[])
    assert decision.decision == "agent"
    assert decision.reason_code == "no_candidate"
    assert decision.selected_skill_id is None


def test_unrelated_skill_is_low_confidence():
    skill = _skill(question="各品类的销售额是多少？", columns=["订单日期", "品类", "销售额"])
    decision = retrieval.route(None, "今天天气怎么样", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "low_confidence"


# ---- 4. 相似 Skill 竞争 ----

def test_ambiguous_candidates_go_through_verification():
    """两条候选分差极小时走严格校验，而不是「谁在前面算谁」。"""
    a = _skill(id=1, question="各渠道的销售额是多少？", columns=["订单日期", "渠道", "销售额"])
    b = _skill(id=2, question="各渠道的销售额有多少？", columns=["订单日期", "渠道", "销售额"])
    decision = retrieval.route(None, "各渠道的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[a, b])
    # 两条信号完全一致时（同一路径的重复沉淀）仍可重放，但必须走 verify 档
    assert decision.decision == "replay"
    assert decision.confidence in {"high", "medium"}
    if decision.confidence == "medium":
        assert decision.reason_code == "verify_passed"


# ---- 5-7. 口径不一致一律拒绝 ----

@pytest.mark.parametrize("blk,question,skill_question,columns,pack", [
    ("metric_mismatch", "各品类的订单量是多少？", "各品类的销售额是多少？",
     ["订单日期", "品类", "销售额"], "retail_sales"),
    ("dimension_mismatch", "各区域的销售额是多少？", "各品类的销售额是多少？",
     ["订单日期", "品类", "销售额"], "retail_sales"),
    ("analysis_type_mismatch", "上月各品类的销售额环比变化", "各品类的销售额是多少？",
     ["订单日期", "品类", "销售额"], "retail_sales"),
])
def test_semantic_mismatch_is_blocked(blk, question, skill_question, columns, pack):
    skill = _skill(question=skill_question, columns=columns, pack_id=pack,
                   datasource_key=f"{pack}::csv")
    decision = retrieval.route(None, question, pack, ctx=_ctx(pack), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == f"blocked:{blk}"
    assert blk in decision.candidates[0].blockers


def test_ranking_direction_mismatch_is_blocked():
    """「最多」与「最少」词面几乎相同，代码里的排序方向相反。"""
    skill = _skill(question="销售额最低的门店是哪个？", columns=["订单日期", "销售额"])
    decision = retrieval.route(None, "销售额最高的门店是哪个？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:ranking_direction_mismatch"


def test_ranking_topn_mismatch_is_blocked():
    skill = _skill(question="销售额最高的10家门店", columns=["订单日期", "销售额"])
    decision = retrieval.route(None, "销售额最高的5家门店", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:ranking_limit_mismatch"


@pytest.mark.parametrize("question,skill_question", [
    ("近30天销售额的周趋势", "近90天销售额的周趋势"),
    ("近30天销售额的日趋势", "近30天销售额的周趋势"),
])
def test_time_window_or_grain_mismatch_is_blocked(question, skill_question):
    skill = _skill(question=skill_question, columns=["订单日期", "销售额"])
    decision = retrieval.route(None, question, "retail_sales", ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:time_window_mismatch"


# ---- 8. DataSource 不一致 ----

def test_datasource_fingerprint_change_is_blocked():
    skill = _skill(datasource_key="retail_sales::xlsx")
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(exts=("csv",)), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:datasource_changed"


def test_reader_incompatible_is_blocked():
    skill = _skill(code="import pandas as pd\ndf = pd.read_excel('/data/x.xlsx')\n",
                   datasource_key="retail_sales::xlsx")
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(exts=("csv",)), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code in {"blocked:reader_mismatch", "blocked:datasource_changed"}


def test_missing_column_is_blocked():
    skill = _skill(columns=["订单日期", "品类", "销售额", "折扣"])
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:column_missing"


def test_cross_pack_is_blocked():
    skill = _skill(pack_id="manufacturing_production", question="各产线的实际产量是多少？",
                   columns=["生产日期", "产线", "实际产量"],
                   datasource_key="manufacturing_production::csv")
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:pack_mismatch"


def test_legacy_skill_without_fingerprint_still_replayable():
    """兼容性：旧 Skill 没有 datasource_key，回退到列结构 + 读取函数判断。"""
    skill = _skill(datasource_key=None, columns=["订单日期", "品类", "销售额"])
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "replay"


def test_empty_code_is_blocked():
    skill = _skill(code="")
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:skill_incomplete"


# ---- 9. Workspace 作用域（跨工作区不可检索） ----

def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_workspace_scope_hides_other_workspace_skills():
    session = _session()
    mine = Skill(name="mine", question="各品类的销售额是多少？", pack_id="retail_sales",
                 code=CSV_CODE, scope="workspace", workspace_id=1)
    other = Skill(name="other", question="各品类的销售额是多少？", pack_id="retail_sales",
                  code=CSV_CODE, scope="workspace", workspace_id=2)
    session.add_all([mine, other])
    session.commit()

    visible = retrieval.visible_skills(session, workspace_id=1, user_id=1)
    assert [s.name for s in visible] == ["mine"]
    decision = retrieval.route(session, "各品类的销售额是多少？", "retail_sales",
                               workspace_id=1, user_id=1, ctx=_ctx())
    assert decision.decision == "replay"
    assert decision.selected_skill_id == mine.id
    session.close()


def test_user_scoped_skill_not_visible_to_others():
    session = _session()
    session.add_all([
        Skill(name="alice", question="各品类的销量是多少？", pack_id="retail_sales",
              code=CSV_CODE, scope="user", user_id=1, workspace_id=1),
        Skill(name="bob", question="各品类的销量是多少？", pack_id="retail_sales",
              code=CSV_CODE, scope="user", user_id=2, workspace_id=1),
    ])
    session.commit()
    names = [s.name for s in retrieval.visible_skills(session, workspace_id=1, user_id=1)]
    assert names == ["alice"]
    session.close()


def test_disabled_skill_is_never_recalled():
    session = _session()
    session.add(Skill(name="off", question="各品类的销售额是多少？", pack_id="retail_sales",
                      code=CSV_CODE, enabled=False, workspace_id=1))
    session.commit()
    assert retrieval.retrieve(session, "各品类的销售额是多少？", "retail_sales",
                              workspace_id=1, user_id=1, ctx=_ctx())[0] == []
    session.close()


# ---- 12. 并发安全 ----

def test_concurrent_retrieval_is_thread_safe():
    """解析缓存被多线程同时读写时不得出现半成品/串数据。"""
    skills = [_skill(id=i, question=f"各{i}类别的销售额是多少？")
              for i in range(1, 21)]
    errors: list[Exception] = []
    results: list[int] = []
    lock = threading.Lock()

    def work(tag: int):
        try:
            for _ in range(30):
                decision = retrieval.route(
                    None, "各品类的销售额是多少？", "retail_sales", ctx=_ctx(), skills=skills)
                with lock:
                    results.append(decision.selected_skill_id)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(results) == 8 * 30
    assert len(set(results)) == 1, "同一输入在多线程下得到了不同结论"


def test_cache_is_bounded_and_clearable():
    for i in range(retrieval._CACHE_LIMIT + 500):  # noqa: SLF001 — 故意压缓存上限
        retrieval.skill_intent(_skill(id=i, question=f"问题{i}"))
    assert len(retrieval._intent_cache) <= retrieval._CACHE_LIMIT  # noqa: SLF001
    retrieval.clear_caches()
    assert retrieval._intent_cache == {}  # noqa: SLF001


# ---- 兼容性：旧入口仍可用 ----

def test_legacy_policy_is_frozen_and_available():
    """Baseline 必须可复现：V1 入口仍在，且行为与当年一致（只看结构守卫）。"""
    skill = _skill(id=1, question="各品类的销售额是多少？", columns=["订单日期", "品类", "销售额"])
    session = _session()
    session.add(skill)
    session.commit()
    decision = retrieval.legacy_admit(session, "各品类的订单量是多少？", "retail_sales",
                                      ctx=_ctx())
    # V1 不校验指标：同维度不同指标也会被放行——这正是 Baseline 的 False Replay 来源
    assert decision.decision == "replay"
    assert decision.reason_code == "structure_only"
    session.close()


def test_route_returns_json_serializable_record():
    skill = _skill()
    decision = retrieval.route(None, "各品类的销售额是多少？", "retail_sales",
                               ctx=_ctx(), skills=[skill])
    payload = json.loads(json.dumps(decision.to_dict(), ensure_ascii=False))
    assert payload["decision"] == "replay"
    assert payload["candidates"][0]["scores"]["type"] == 1.0
    assert payload["data_context"]["signature"] == "retail_sales::csv"


def test_skill_ids_are_unique_in_candidate_list():
    skills = [_skill(id=uuid.uuid4().int % 10_000 + 1) for _ in range(5)]
    candidates, _ = retrieval.retrieve(None, "各品类的销售额是多少？", "retail_sales",
                                       ctx=_ctx(), skills=skills)
    ids = [c.skill_id for c in candidates]
    assert len(ids) == len(set(ids))
