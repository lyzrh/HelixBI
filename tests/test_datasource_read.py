"""datasource 服务：智能文件读取（编码/分隔符/JSON）、清洗与行数统计。"""

import pandas as pd

from backend.services.datasource import (
    _clean_rows, _read_file, count_rows, detect_encoding_and_sep, detect_file_type,
)


def test_read_utf8_csv(csv_utf8):
    df = _read_file(csv_utf8)
    assert list(df.columns) == ["日期", "品类", "销售额"]
    assert len(df) == 2


def test_read_gbk_csv(csv_gbk):
    df = _read_file(csv_gbk)
    assert list(df.columns) == ["日期", "品类", "销售额"]
    assert df.loc[0, "品类"] == "饮料"


def test_read_semicolon_csv(csv_semicolon):
    df = _read_file(csv_semicolon)
    assert list(df.columns) == ["日期", "品类", "销售额"]


def test_read_tsv(tsv_file):
    df = _read_file(tsv_file)
    assert list(df.columns) == ["日期", "品类", "销售额"]


def test_read_json_records(json_records):
    df = _read_file(json_records)
    assert list(df.columns) == ["日期", "品类", "销售额"]
    assert len(df) == 2


def test_read_jsonl(jsonl_file):
    df = _read_file(jsonl_file)
    assert list(df.columns) == ["日期", "品类", "销售额"]
    assert len(df) == 2


def test_read_xlsx(xlsx_file):
    df = _read_file(xlsx_file)
    assert list(df.columns) == ["日期", "品类", "销售额"]
    assert df.loc[1, "销售额"] == 200


def test_read_file_nrows(csv_utf8):
    assert len(_read_file(csv_utf8, nrows=1)) == 1


def test_detect_encoding_and_sep_gbk(csv_gbk):
    encoding, sep = detect_encoding_and_sep(csv_gbk)
    assert encoding == "gb18030"
    assert sep == ","


def test_detect_encoding_and_sep_tsv(tsv_file):
    _, sep = detect_encoding_and_sep(tsv_file)
    assert sep == "\t"


def test_detect_file_type_json(json_records, jsonl_file):
    assert detect_file_type(json_records) == "json"
    assert detect_file_type(jsonl_file) == "jsonl"


def test_detect_file_type_exts(xlsx_file, tsv_file):
    assert detect_file_type(xlsx_file) == "xlsx"
    assert detect_file_type(tsv_file) == "tsv"


def test_clean_rows_json_safe():
    df = pd.DataFrame({
        "a": [1.0, float("nan")],
        "b": pd.to_datetime(["2026-01-01", None]),
    })
    rows = _clean_rows(df)
    assert rows[0]["a"] == 1.0
    assert rows[1]["a"] is None
    assert isinstance(rows[0]["b"], str)


def test_count_rows_fast_paths(csv_utf8, xlsx_file, json_records, jsonl_file):
    assert count_rows(csv_utf8) == 2
    assert count_rows(xlsx_file) == 2
    assert count_rows(json_records) == 2
    assert count_rows(jsonl_file) == 2


def test_count_rows_missing_file():
    assert count_rows("no/such/file.csv") == 0
