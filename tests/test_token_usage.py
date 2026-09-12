"""token 用量提取：langchain usage_metadata 是 dict，旧代码按属性读恒为 0。"""

from backend.agent.graph import _extract_usage


class FakeResponse:
    def __init__(self, usage_metadata=None, response_metadata=None):
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


def test_usage_metadata_dict():
    resp = FakeResponse(usage_metadata={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165})
    assert _extract_usage(resp) == (120, 45, 0.0)


def test_fallback_openai_token_usage():
    resp = FakeResponse(response_metadata={"token_usage": {"prompt_tokens": 30, "completion_tokens": 8}})
    assert _extract_usage(resp) == (30, 8, 0.0)


def test_missing_usage_returns_zero():
    assert _extract_usage(FakeResponse()) == (0, 0, 0.0)


def test_none_metadata_returns_zero():
    assert _extract_usage(FakeResponse(usage_metadata=None)) == (0, 0, 0.0)
