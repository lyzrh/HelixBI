"""评估报告本身的完整性：数据集可信 + 结果不编造。

这一组测试保护的是**评估系统的可信度**：
- 数据集里的人工标注必须与语义包对得上（否则指标没有意义）；
- 依赖外部资源的阶段没跑就必须标记 skipped 并说明原因，绝不能填一个好看的数字。
"""

from backend.evaluation import runner
from backend.evaluation.datasets import all_cases, load_all
from backend.evaluation.metrics import render_report
from backend.semantic import list_packs
from backend.semantic.registry import load_pack

REQUIRED_SECTIONS = ("dataset", "semantic", "planning", "skills", "replay",
                     "execution", "self_repair", "end_to_end")
OFFLINE_STAGES = ("semantic", "planning", "skills", "replay")
GATED_STAGES = ("execution", "self_repair", "end_to_end")

# 各阶段用于比较的"主指标"字段名
PRIMARY_METRIC = {
    "semantic": "accuracy",
    "planning": "coverage",
    "skills": "top1_accuracy",
    "replay": "eligibility_rate",
}


def test_dataset_files_are_discoverable():
    names = {ds["name"] for ds in load_all()}
    assert {"retail_questions", "manufacturing_questions", "hard_cases"} <= names


def test_dataset_size_is_meaningful():
    assert len(all_cases()) >= 50, "固定问题集至少要 50 条才具备统计意义"


def test_case_ids_are_unique():
    ids = [c["id"] for c in all_cases()]
    assert len(ids) == len(set(ids))


def test_every_case_is_well_formed():
    pack_ids = {p["id"] for p in list_packs()}
    for case in all_cases():
        assert case.get("id") and case.get("question")
        assert case.get("pack") in pack_ids, f"{case['id']} 的 pack 不存在"
        expected = case.get("expected")
        assert isinstance(expected, dict)
        for key in ("metrics", "dimensions", "comparison", "analysis_type"):
            assert key in expected, f"{case['id']} 缺少 {key}"


def test_labels_align_with_semantic_packs():
    """标注里的指标 / 维度名必须是语义包里的 canonical name，不能自己造词。"""
    for case in all_cases():
        pack = load_pack(case["pack"]) or {}
        metric_names = {m["name"] for m in pack.get("metrics", [])}
        dimension_names = {d["name"] for d in pack.get("dimensions", [])}
        for name in case["expected"]["metrics"]:
            assert name in metric_names, f"{case['id']} 指标「{name}」不在语义包里"
        for name in case["expected"]["dimensions"]:
            assert name in dimension_names, f"{case['id']} 维度「{name}」不在语义包里"


def test_comparison_values_are_from_enum():
    for case in all_cases():
        assert case["expected"]["comparison"] in {"none", "yoy", "mom"}


def test_report_has_all_sections(report):
    for key in REQUIRED_SECTIONS:
        assert key in report, f"报告缺少 {key}"


def test_gated_stages_never_fabricate_numbers(report):
    """未采集的阶段不得出现成功率数字——只有 status=ok 才允许有 rate。"""
    for key in GATED_STAGES:
        stage = report[key]
        if stage.get("status") == "skipped":
            assert stage.get("reason"), f"{key} 跳过了却没说原因"
            assert "rate" not in stage
        else:
            assert stage["status"] == "ok"
            assert 0.0 <= stage["rate"] <= 1.0


def test_offline_stages_always_present(report):
    for key in OFFLINE_STAGES:
        assert report[key].get("status") == "ok", f"{key} 离线阶段不应被跳过"


def test_render_report_contains_key_metrics(report):
    text = render_report(report)
    assert "Evaluation Report" in text
    assert "语义解析准确率" in text
    assert "Skill 匹配 Top1 准确率" in text
    assert "未采集" in text, "未采集阶段必须显式标注，而不是留空"


def test_run_all_is_reproducible():
    """离线阶段是确定性的：两次运行的主指标必须完全一致。"""
    first, second = runner.run_all(), runner.run_all()
    for key in OFFLINE_STAGES:
        metric = PRIMARY_METRIC[key]
        assert first[key][metric] == second[key][metric], f"{key} 不确定"
