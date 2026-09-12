"""评估测试的公共 fixture。

这些测试把「评估指标」变成 CI 门禁：指标掉下阈值就红，而不是靠人肉感觉。
离线阶段（语义解析 / 规划契约 / Skill 匹配 / 重放准入）在 CI 里必跑；
需要 Docker + LLM 的阶段用 `skipif` 显式跳过，不假装通过。
"""

import pytest

from backend.evaluation import runner
from backend.evaluation.datasets import all_cases, load_all

STANDARD_DATASETS = {"retail_questions", "manufacturing_questions"}
HARD_DATASETS = {"hard_cases"}


@pytest.fixture(scope="session")
def report() -> dict:
    """整份离线评估报告（session 级，只跑一次）。"""
    return runner.run_all(with_pipeline=False)


@pytest.fixture(scope="session")
def standard_cases() -> list[dict]:
    return [c for ds in load_all() if ds["name"] in STANDARD_DATASETS for c in ds["cases"]]


@pytest.fixture(scope="session")
def hard_cases() -> list[dict]:
    return [c for ds in load_all() if ds["name"] in HARD_DATASETS for c in ds["cases"]]


@pytest.fixture(scope="session")
def every_case() -> list[dict]:
    return all_cases()


@pytest.fixture(scope="session")
def pipeline_ready() -> bool:
    return runner.pipeline_blocker() is None


def pytest_configure(config):
    config.addinivalue_line("markers", "pipeline: 需要 Docker 沙箱 + LLM 的端到端评估")
