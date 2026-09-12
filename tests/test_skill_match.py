"""Skill 引擎纯函数：token 匹配、列匹配、读取器兼容、/data 路径归一化。"""

from backend.skills.engine import (
    _columns_match, _normalize_data_paths, _reader_compatible, _tokenize,
)


def test_tokenize_cn_and_en():
    tokens = _tokenize("各品类销售额 GMV")
    assert "品类" in tokens
    assert "gmv" in tokens


def test_columns_match_subset():
    assert _columns_match(["a", "b"], {"a", "b", "c"})
    assert not _columns_match(["a", "d"], {"a", "b", "c"})
    assert not _columns_match([], {"a"})


def test_reader_compatible_csv():
    code = 'df = pd.read_csv("/data/x.csv")'
    assert _reader_compatible(code, ["a1_sales.csv"])
    assert _reader_compatible(code, ["a1_data.tsv"])
    assert not _reader_compatible(code, ["a1_data.xlsx"])
    assert not _reader_compatible(code, ["a1_data.parquet"])


def test_reader_compatible_excel_json():
    xlsx_code = 'df = pd.read_excel("/data/x.xlsx")'
    assert _reader_compatible(xlsx_code, ["a1_data.xlsx"])
    assert not _reader_compatible(xlsx_code, ["a1_data.csv"])
    json_code = 'df = pd.read_json("/data/x.json")'
    assert _reader_compatible(json_code, ["a1_data.jsonl"])
    assert not _reader_compatible(json_code, ["a1_data.csv"])


def test_reader_compatible_no_reader():
    assert _reader_compatible("print(1)", ["a1_data.csv"])


def test_normalize_placeholder():
    files = {"a1_sales.csv": "C:/data/a1_sales.csv"}
    code = 'df = pd.read_csv("/data/{name}")'
    assert "/data/a1_sales.csv" in _normalize_data_paths(code, files)


def test_normalize_legacy_display_name():
    files = {"a1_sales.csv": "C:/data/a1_sales.csv"}
    code = 'df = pd.read_csv("/data/销售数据", parse_dates=["订单日期"])'
    assert _normalize_data_paths(code, files) == \
        'df = pd.read_csv("/data/a1_sales.csv", parse_dates=["订单日期"])'


def test_normalize_keeps_known_paths():
    files = {"a1_sales.csv": "C:/data/a1_sales.csv", "b2_store.csv": "C:/data/b2_store.csv"}
    code = 'a = pd.read_csv("/data/a1_sales.csv")\nb = pd.read_csv("/data/b2_store.csv")'
    assert _normalize_data_paths(code, files) == code
