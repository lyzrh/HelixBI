"""语义包渲染：同义词 / 示例问题进入 prompt，可选指标按数据过滤。"""

from backend.semantic import load_pack, render_semantic_prompt

PACK_ID = "retail_sales"


def test_render_contains_metrics_and_dimensions():
    block = render_semantic_prompt(PACK_ID)
    assert "销售额" in block
    assert "品类" in block
    assert "时间字段" in block


def test_render_contains_synonyms():
    block = render_semantic_prompt(PACK_ID)
    # 销售额的别名（synonyms 去掉同名项）
    assert "别名" in block
    assert "GMV" in block


def test_render_contains_example_questions():
    block = render_semantic_prompt(PACK_ID)
    assert "示例问题" in block
    assert "各品类的总销售额是多少" in block


def test_render_optional_metric_filtered():
    pack = load_pack(PACK_ID)
    optional = [m for m in pack["metrics"] if m.get("optional")]
    assert optional, "测试依赖 retail_sales 存在 optional 指标"
    block = render_semantic_prompt(PACK_ID)
    # 未标记 _present 时 optional 指标不渲染
    assert optional[0]["name"] not in block


def test_render_unknown_pack_empty():
    assert render_semantic_prompt("no_such_pack") == ""
