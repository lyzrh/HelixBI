"""测试公共 fixture：临时数据文件（csv 编码/分隔符变体、json、xlsx）。"""

import json as _json

import pytest


@pytest.fixture
def csv_utf8(tmp_path):
    p = tmp_path / "utf8.csv"
    p.write_text("日期,品类,销售额\n2026-01-01,饮料,100\n2026-01-02,零食,200\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def csv_gbk(tmp_path):
    p = tmp_path / "gbk.csv"
    p.write_text("日期,品类,销售额\n2026-01-01,饮料,100\n2026-01-02,零食,200\n", encoding="gbk")
    return str(p)


@pytest.fixture
def csv_semicolon(tmp_path):
    p = tmp_path / "semi.csv"
    p.write_text("日期;品类;销售额\n2026-01-01;饮料;100\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def tsv_file(tmp_path):
    p = tmp_path / "data.tsv"
    p.write_text("日期\t品类\t销售额\n2026-01-01\t饮料\t100\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def json_records(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(_json.dumps([
        {"日期": "2026-01-01", "品类": "饮料", "销售额": 100},
        {"日期": "2026-01-02", "品类": "零食", "销售额": 200},
    ], ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture
def jsonl_file(tmp_path):
    p = tmp_path / "data.jsonl"
    lines = [
        _json.dumps({"日期": "2026-01-01", "品类": "饮料", "销售额": 100}, ensure_ascii=False),
        _json.dumps({"日期": "2026-01-02", "品类": "零食", "销售额": 200}, ensure_ascii=False),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


@pytest.fixture
def xlsx_file(tmp_path):
    from openpyxl import Workbook

    p = tmp_path / "data.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["日期", "品类", "销售额"])
    ws.append(["2026-01-01", "饮料", 100])
    ws.append(["2026-01-02", "零食", 200])
    wb.save(p)
    return str(p)
