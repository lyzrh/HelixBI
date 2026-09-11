"""上传表单字段编码回收测试：模拟 GBK 客户端经 python-multipart latin-1 误解码的乱码。"""

from backend.routers.datasources import _decode_form_text


def _mojibake(text: str, encoding: str) -> str:
    """模拟 python-multipart 的行为：把 GBK/非 UTF-8 字节按 latin-1 逐字节解码。"""
    return text.encode(encoding).decode("latin-1")


def test_recovers_gbk_mojibake():
    garbled = _mojibake("GBK测试", "gbk")
    assert garbled == "GBK²âÊÔ"
    assert _decode_form_text(garbled) == "GBK测试"


def test_recovers_gbk_filename():
    garbled = _mojibake("销售数据.csv", "gbk")
    assert _decode_form_text(garbled) == "销售数据.csv"


def test_utf8_text_untouched():
    # 浏览器路径：字段本身已是正确的 UTF-8 字符串，含 latin-1 之外字符，原样返回
    assert _decode_form_text("零售数据2026.xlsx") == "零售数据2026.xlsx"


def test_ascii_untouched():
    assert _decode_form_text("sample_sales.csv") == "sample_sales.csv"


def test_undecodable_bytes_kept():
    # latin-1 可逆但既非 utf-8 也非 gb18030 的孤立字节：保持原值不破坏
    assert _decode_form_text("\x81") == "\x81"
