"""Token 计数：把"这次请求大概花多少 token"变成**可计算**的确定性数字。

为什么需要它：

- 成本优化的前提是能定位 token 花在哪一块（语义层？few-shot？数据概况？）；
- LLM 预算（`backend/agent/budget.py`）必须在**调用之前**判断"还够不够"，而
  provider 返回的 usage 是调用之后才有的——所以预检只能用本地计数；
- 评估里的 Token / Query 若靠"估算公式"就不可复现，用真实 tokenizer 计数才站得住。

诚实性约定：优先用 tiktoken（与 OpenAI 系模型同族的 BPE）；tiktoken 不可用
（未安装 / 离线无法取编码文件）时**降级为中英分档启发式估算**，并把所用方法
写进 `method()`，调用方（评估报告 / trace）必须如实标注，不能把估算说成实测。
"""

from __future__ import annotations

import functools
import threading

# tiktoken 的编码对象创建有成本（且首次可能需要读缓存文件），进程内复用一次即可
_lock = threading.Lock()
_encoder = None
_encoder_ready = False

# 已知与 tiktoken 不同族 / 未验证的模型名不做映射，避免"看起来精确"的错误计数
_ENCODING_PREFERENCE = ("o200k_base", "cl100k_base")


def _get_encoder():
    """惰性获取编码器；失败返回 None（不抛异常，调用方走估算）。"""
    global _encoder, _encoder_ready
    if _encoder_ready:
        return _encoder
    with _lock:
        if _encoder_ready:
            return _encoder
        try:  # pragma: no cover - 取决于运行环境是否装有 tiktoken
            import tiktoken

            for name in _ENCODING_PREFERENCE:
                try:
                    _encoder = tiktoken.get_encoding(name)
                    break
                except Exception:
                    continue
        except Exception:
            _encoder = None
        _encoder_ready = True
    return _encoder


def method() -> str:
    """当前生效的计数方法（写进 trace / 评估报告，禁止让估算冒充实测）。"""
    encoder = _get_encoder()
    if encoder is not None:
        return f"tiktoken:{encoder.name}"
    return "heuristic:cjk=1/char,ascii=0.25/char"


def is_exact() -> bool:
    return _get_encoder() is not None


def _estimate(text: str) -> int:
    """启发式估算：CJK 一字 ≈ 1 token，其余按 4 字符 ≈ 1 token。"""
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef":
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


@functools.lru_cache(maxsize=4096)
def _count_cached(text: str) -> int:
    encoder = _get_encoder()
    if encoder is None:
        return _estimate(text)
    try:
        return len(encoder.encode(text, disallowed_special=()))
    except Exception:  # pragma: no cover - 编码失败不该打断分析
        return _estimate(text)


def count_tokens(text: str | None) -> int:
    """文本 token 数（空文本 0）。同一文本重复计数命中缓存。"""
    if not text:
        return 0
    return _count_cached(text)


def count_messages(messages: list) -> int:
    """消息列表的 token 数：内容合计 + 每条消息的固定包装开销。

    包装开销（role / 分隔符等）是各家 API 的私有细节，这里按常见的
    "每条消息 4 token" 计——它只用于预算预检与相对比较，不冒充 provider 计费值。
    """
    total = 0
    for message in messages or []:
        total += count_tokens(getattr(message, "content", "") or "")
        total += 4
    return total


def clear_cache() -> None:
    """清空计数缓存（测试用：换编码器或做冷启动测量时）。"""
    _count_cached.cache_clear()


__all__ = [
    "clear_cache", "count_messages", "count_tokens", "is_exact", "method",
]
