"""CI Regression Gate：Skill Retrieval / Replay Admission 不得回退。

门禁的取值原则：
- 阈值只能**收紧**，不能为了让某次改动过而放宽；
- 与 Baseline（V1）比较时，比的是同一批用例、同一候选池；
- 最重要的门禁是 `False Replay`——「系统宁愿放弃 Replay，也不能错误 Replay」。

对应交付要求里的 CI Gate 四项：Replay Precision 不低于 baseline、False Replay 不恶化、
RBAC 测试必过（在本仓库由 `tests/test_auth_rbac.py` 承担，这里再锁一条纵深防御）、
核心评估无明显回归。
"""

import pytest

from backend.evaluation import runner
from backend.evaluation.datasets import admission_cases, all_cases

# ---- 门禁阈值（只收紧，不放宽） ----

GATE = {
    "coarse_top1": 0.75,          # V2 召回 Top1（与既有门禁 0.70 相比收紧）
    "coarse_recall_at_2": 0.80,   # 不低于既有门禁 0.80
    "fine_top1": 0.95,
    "fine_replay_precision": 0.95,
    "fine_replay_recall": 0.90,
    "fine_false_replay_rate": 0.0,
    "admission_accuracy": 0.90,
    "admission_reason_accuracy": 0.90,
    "admission_false_accept": 0,
}


@pytest.fixture(scope="session")
def retrieval() -> dict:
    return runner.eval_retrieval_admission()


# ---- 1. 检索不回归 ----

def test_coarse_top1_above_gate(retrieval):
    v2 = retrieval["v2"]["coarse"]
    assert v2["top1_accuracy"] >= GATE["coarse_top1"], v2
    assert v2["recall_at_2"] >= GATE["coarse_recall_at_2"], v2


def test_v2_retrieval_beats_baseline(retrieval):
    """V2 的 Top1 必须严格优于 V1（否则这次改造没有价值）。"""
    assert retrieval["v2"]["coarse"]["top1_accuracy"] > \
        retrieval["v1"]["coarse"]["top1_accuracy"]
    assert retrieval["v2"]["fine"]["top1_accuracy"] > \
        retrieval["v1"]["fine"]["top1_accuracy"]


# ---- 2. 重放质量：Precision 不得低于 baseline，False Replay 不得恶化 ----

def test_replay_precision_not_below_baseline(retrieval):
    v1, v2 = retrieval["v1"]["fine"], retrieval["v2"]["fine"]
    assert v2["replay_precision"] >= v1["replay_precision"], (v1, v2)
    assert v2["replay_precision"] >= GATE["fine_replay_precision"], v2


def test_false_replay_never_worse_than_baseline(retrieval):
    v1, v2 = retrieval["v1"]["fine"], retrieval["v2"]["fine"]
    assert v2["false_replay_rate"] <= v1["false_replay_rate"]
    assert v2["false_replay_rate"] <= GATE["fine_false_replay_rate"], v2
    assert v2["false_replay_cases"] == [], f"出现错误重放：{v2['false_replay_cases']}"


def test_replay_recall_above_gate(retrieval):
    v2 = retrieval["v2"]["fine"]
    assert v2["replay_recall"] >= GATE["fine_replay_recall"], v2


# ---- 3. 准入判定（对抗集） ----

def test_admission_accuracy_above_gate(retrieval):
    v2 = retrieval["v2"]["admission"]
    assert v2["admission_accuracy"] >= GATE["admission_accuracy"], v2["failures"]
    assert v2["decision_and_reason_accuracy"] >= GATE["admission_reason_accuracy"], \
        v2["failures"]


def test_no_false_accept_on_adversarial_cases(retrieval):
    """对抗集的全部意义：不允许把「相似但语义不同」的 Skill 放行重放。"""
    v2 = retrieval["v2"]["admission"]
    assert v2["false_accept"] <= GATE["admission_false_accept"], v2["failures"]
    assert v2["false_reject"] == 0, "不该把真正可复用的路径也拒掉"


def test_admission_beats_baseline_on_adversarial(retrieval):
    assert retrieval["v2"]["admission"]["admission_accuracy"] > \
        retrieval["v1"]["admission"]["admission_accuracy"]


# ---- 4. 效率：把误重放折算成 Agent 成本后不得更高 ----

def test_efficiency_not_worse_when_misreplay_is_accounted(retrieval):
    e1, e2 = retrieval["v1"]["efficiency"], retrieval["v2"]["efficiency"]
    assert e2["llm_calls_per_query_incl_misreplay"] <= \
        e1["llm_calls_per_query_incl_misreplay"], (e1, e2)
    assert e2["avg_tokens_per_query_incl_misreplay"] <= \
        e1["avg_tokens_per_query_incl_misreplay"], (e1, e2)


def test_efficiency_reports_profile_and_unmeasured_fields(retrieval):
    """画像来源必须写明；测不到的量（沙箱耗时）必须标注，而不是填数字。"""
    e2 = retrieval["v2"]["efficiency"]
    assert e2["profile"]["source"]
    assert e2["sandbox_ms"] is None and "sandbox_ms" in e2["unmeasured"]
    assert e2["cost_usd_per_query"] is None or e2["price_configured"]


# ---- 5. 评估本身可复现 + 数据集完整 ----

def test_retrieval_stage_is_reproducible():
    """离线阶段是确定性的：两次运行的主指标必须完全一致（否则门禁无意义）。"""
    first, second = runner.eval_retrieval_admission(), runner.eval_retrieval_admission()
    for policy in ("v1", "v2"):
        for stage in ("fine", "coarse", "admission"):
            assert first[policy][stage] == second[policy][stage], f"{policy}/{stage} 不确定"


def test_adversarial_dataset_covers_required_scenarios():
    """对抗集必须覆盖交付要求里点名的场景，缺一类就说明门禁有盲区。"""
    kinds = " ".join(c["kind"] + c["id"] for c in admission_cases())
    for keyword in ("不同维度", "不同指标", "排序方向", "TopN", "时间", "粒度",
                    "数据源", "筛选", "同义词", "竞争", "新指标"):
        assert keyword in kinds, f"对抗集缺少场景：{keyword}"
    assert len(admission_cases()) >= 20


def test_adversarial_cases_are_well_formed():
    pack_ids = {c["pack"] for c in all_cases()}
    for case in admission_cases():
        assert case["id"] and case["question"]
        assert case["candidates"], f"{case['id']} 没有候选池"
        expect = case["expect"]
        assert expect["decision"] in {"replay", "agent"}
        assert "reason_code" in expect
        if expect["decision"] == "agent":
            assert expect["reason_code"].startswith(("blocked:", "verify_failed:",
                                                     "low_confidence")), case["id"]
        if expect.get("selected_skill"):
            keys = {c["key"] for c in case["candidates"]}
            assert expect["selected_skill"] in keys, case["id"]
    assert pack_ids  # 至少要有语义包，否则上面的校验没有意义


def test_admission_config_is_sane():
    """配置门：阈值有序、权重为正——防止有人把某一路权重设成 0 或负数把策略搞坏。"""
    from backend import config

    assert 0 < config.SKILL_ADMISSION_LOW < config.SKILL_ADMISSION_HIGH <= 1.0
    assert 0 <= config.SKILL_ADMISSION_MARGIN < config.SKILL_ADMISSION_HIGH
    assert config.SKILL_RETRIEVAL_TOP_K >= 2
    weights = config.SKILL_RETRIEVAL_WEIGHTS
    assert all(isinstance(v, float) and v > 0 for v in weights.values()), weights
    # 六个以上信号里，语义 / 指标 / 维度必须占主导（否则可解释性会退化成词面匹配）
    core = weights["semantic"] + weights["metric"] + weights["dimension"]
    assert core >= 0.5, weights
