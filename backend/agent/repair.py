"""Self-Repair V2：错误分类 → 定向修复 → 有界重试 → 兜底。

## 为什么要有这个模块（Problem）

旧实现的自修复只有一条路径：`execute` 失败 → 把 `code + stdout + stderr`
塞进同一个 `FIX_USER_TMPL` → 让 LLM 重新生成 → 再执行，最多 `MAX_FIX_ATTEMPTS` 次。
三个后果：

1. **提示词与错误无关**：列名写错、`to_datetime` 类型不对、超时、结果为空，
   四种完全不同的病因拿到的是同一段"请分析错误原因"。
2. **不识别复读**：LLM 第二次犯同样的错（甚至一模一样的 traceback），系统照旧
   再烧一次 token 重试——`MAX_FIX_ATTEMPTS` 成了唯一刹车。
3. **不可解释**：`attempts=3` 只能说明"修了 3 次"，回答不了"为什么修了 2 次才成功、
   第一次错在哪、第三次换了什么策略、复读发生在第几次"。

## V2 的设计

```
generate → sandbox execute → classify（分类 + 验收门）→ decide（策略 + 有界额度）
   ↑                                                          │
   └──────────── targeted repair prompt（按错误类型定制） ──────┘
                                        │ 停止 / 额度用尽 / 复读
                                        └→ 交给 summarize（结构化失败，不抛错）
```

四件事全部**确定性、零 token**（不新增任何 LLM 调用，只是把已有那次调用的
prompt 换成针对性的）：

- `classify_error()` —— 把执行态映射到 9 类错误 + 可枚举的 error signature；
- `strategy_for()` —— 每类错误一份**定向修复提示**（列不存在 → 对齐真实 schema；
  类型错误 → dtype / 聚合 / 转换；空结果 → 过滤条件 / 时间范围 / 字段取值；
  语法错误 → 只修代码结构；超时 / 资源 → 降计算量与内存）；
- `decide_repair()` —— 有界重试：全局 `MAX_FIX_ATTEMPTS` 上限 + 每类错误独立额度 +
  **复读检测**（相同 signature 立即停；连续同类错误视为无进展，提前终止）；
- `RepairDecision` —— 决策与原因码进 `Run.trace.self_repair`，可回答
  "这次为什么修 2 次 / 为什么直接放弃"。

`policy="v1"` 是**冻结的旧策略**（无差别重试、不分类、不复读检测），只为
Baseline 对比与回归留档，与 `skills/retrieval.py` 的 `legacy_*` 同一模式，
不要改动它——改了基准就不可复现了。

## 不变式

- 分类与决策只看执行态（`ok / stderr / exit_code / 产物`），不调 LLM、不读库；
- 复读或额度用尽一律**停止重试**，最终仍由 `summarize` 给出结构化失败结论，
  不把异常抛给用户；
- 上限永远是 `config.MAX_FIX_ATTEMPTS`（每类额度只会在它之内更早收手）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 错误类别（覆盖需求列出的 9 类 + 环境不可用这一类「不该重试」的情况）
# ---------------------------------------------------------------------------

CATEGORY_SYNTAX = "syntax_error"          # SyntaxError / IndentationError
CATEGORY_NAME = "name_error"              # NameError / UnboundLocalError / AttributeError
CATEGORY_COLUMN = "column_error"          # KeyError / ColumnNotFound / not in index
CATEGORY_TYPE = "type_error"              # TypeError / ValueError / dtype 不匹配
CATEGORY_EMPTY = "empty_result"           # 执行成功但没有可用结果
CATEGORY_TIMEOUT = "timeout"              # 沙箱执行超时
CATEGORY_RESOURCE = "resource_limit"      # OOM / 资源超限被杀
CATEGORY_CONTRACT = "contract_error"      # dahelper / 结果回传契约错误
CATEGORY_UNKNOWN = "unknown"              # 其他
CATEGORY_ENVIRONMENT = "environment"      # 沙箱环境不可用（Docker 未运行）——不可修复

CATEGORIES = (
    CATEGORY_SYNTAX, CATEGORY_NAME, CATEGORY_COLUMN, CATEGORY_TYPE, CATEGORY_EMPTY,
    CATEGORY_TIMEOUT, CATEGORY_RESOURCE, CATEGORY_CONTRACT, CATEGORY_UNKNOWN,
    CATEGORY_ENVIRONMENT,
)

CATEGORY_LABELS = {
    CATEGORY_SYNTAX: "语法错误",
    CATEGORY_NAME: "名称/属性错误",
    CATEGORY_COLUMN: "列不存在",
    CATEGORY_TYPE: "类型/取值错误",
    CATEGORY_EMPTY: "结果为空",
    CATEGORY_TIMEOUT: "执行超时",
    CATEGORY_RESOURCE: "资源超限",
    CATEGORY_CONTRACT: "结果回传契约错误",
    CATEGORY_UNKNOWN: "其他错误",
    CATEGORY_ENVIRONMENT: "运行环境不可用",
}

# 每类错误的**独立修复额度**：只会在全局 MAX_FIX_ATTEMPTS 之内更早收手，
# 用来避免把整个预算耗在同一个病因上（超时再修一次通常还是超时）。
CATEGORY_ATTEMPT_CAP = {
    CATEGORY_SYNTAX: 3,
    CATEGORY_NAME: 3,
    CATEGORY_COLUMN: 3,
    CATEGORY_TYPE: 3,
    CATEGORY_EMPTY: 2,
    CATEGORY_TIMEOUT: 1,
    CATEGORY_RESOURCE: 1,
    CATEGORY_CONTRACT: 1,
    CATEGORY_UNKNOWN: 1,
    CATEGORY_ENVIRONMENT: 0,
}

# ---------------------------------------------------------------------------
# error signature：让「是不是同一个错误」可判定
# ---------------------------------------------------------------------------

_EXC_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Exit|Interrupt))\s*:?\s*(.*)$",
    re.MULTILINE,
)
_LOCATION_RE = re.compile(r'File "[^"]*", line \d+[^\n]*')
_NUMBER_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")


def normalize_message(message: str, limit: int = 200) -> str:
    """把报错正文归一化：去掉定位信息 / 内存地址 / 具体数字。

    这样「第 37 行 KeyError」与「第 41 行 KeyError」会被认成同一个错误，
    而复读检测真正想抓的正是这种"换个行号又犯一次"。
    """
    text = str(message or "")
    text = _LOCATION_RE.sub(" ", text)
    text = _ADDR_RE.sub("0xADDR", text)
    text = _NUMBER_RE.sub("N", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def last_exception(stderr: str) -> tuple[str, str]:
    """从 traceback 里取最后一个异常类型与正文；取不到则返回 ("", stderr)。"""
    matches = _EXC_RE.findall(str(stderr or ""))
    if matches:
        exc, msg = matches[-1]
        return exc.strip(), msg.strip()
    return "", str(stderr or "").strip()


def error_signature(category: str, message: str) -> str:
    """`类别|归一化正文` —— 相同错误稳定得到同一个字符串（进 trace，可对比）。"""
    return f"{category}|{normalize_message(message)}"


# ---------------------------------------------------------------------------
# 执行态 → 错误分类
# ---------------------------------------------------------------------------

_PANDAS_MARKERS = {
    CATEGORY_COLUMN: (
        "not in index", "no such column", "does not exist", "not found in axis",
        "usecols do not match", "cannot find column",
    ),
    CATEGORY_RESOURCE: (
        "memoryerror", "cannot allocate memory", "out of memory", "oom",
        "killed", "std::bad_alloc",
    ),
    CATEGORY_TIMEOUT: ("timed out", "执行超时", "timeoutexpired"),
    CATEGORY_CONTRACT: ("dahelper", "result.json", "save_table", "save_chart", "save_text"),
    CATEGORY_NAME: ("is not defined", "has no attribute", "no attribute"),
    CATEGORY_TYPE: (
        "could not convert", "cannot convert", "unsupported operand",
        "invalid literal for", "must be str", "could not infer format",
        "numpy.float64' object is not iterable", "out of bounds datetime",
        "dataframe constructor not properly called",
    ),
    CATEGORY_SYNTAX: ("unexpected eof", "invalid syntax", "unexpected indent",
                      "expected ':'", "unmatched", "was never closed"),
}

_EXC_TO_CATEGORY = {
    "SyntaxError": CATEGORY_SYNTAX,
    "IndentationError": CATEGORY_SYNTAX,
    "TabError": CATEGORY_SYNTAX,
    "NameError": CATEGORY_NAME,
    "UnboundLocalError": CATEGORY_NAME,
    "AttributeError": CATEGORY_NAME,
    "ImportError": CATEGORY_NAME,
    "ModuleNotFoundError": CATEGORY_NAME,
    "KeyError": CATEGORY_COLUMN,
    "ColumnNotFoundError": CATEGORY_COLUMN,
    "IndexError": CATEGORY_COLUMN,
    "TypeError": CATEGORY_TYPE,
    "ValueError": CATEGORY_TYPE,
    "OutOfBoundsDatetime": CATEGORY_TYPE,
    "IntCastingNaNError": CATEGORY_TYPE,
    "MemoryError": CATEGORY_RESOURCE,
}

# 注：列缺失优先于名称错误——`KeyError: '销售额'` 的异常类型本身就是 KeyError，
# 但 `df.销售额` 这类属性写法会报 AttributeError，两者都应当走"对齐真实 schema"的修复。
_MARKER_ORDER = (CATEGORY_COLUMN, CATEGORY_SYNTAX, CATEGORY_TYPE, CATEGORY_NAME)


@dataclass(frozen=True)
class ErrorInfo:
    """一次执行的错误画像（分类 + 指纹 + 是否可修）。"""

    category: str
    signature: str
    message: str
    exception: str = ""
    accepted: bool = False
    retryable: bool = True
    detail: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def to_dict(self) -> dict:
        return {
            "category": self.category, "label": self.label,
            "signature": self.signature, "exception": self.exception,
            "message": self.message[:400], "accepted": self.accepted,
            "retryable": self.retryable, "detail": dict(self.detail),
        }


def classify_error(execution: dict, gate: dict | None = None) -> ErrorInfo:
    """把沙箱执行态映射为 `ErrorInfo`（确定性、零 token）。

    `gate` 是 `agent.acceptance.acceptance_gate()` 的结论；不传则自行计算，
    保证「验收不通过」永远与分类同源。
    """
    from backend.agent.acceptance import acceptance_gate

    execution = execution or {}
    gate = gate or acceptance_gate(execution)
    ok = bool(execution.get("ok"))
    stderr = str(execution.get("stderr") or "")
    stdout = str(execution.get("stdout") or "")
    exit_code = execution.get("exit_code")
    lower = stderr.lower()

    def make(category: str, message: str, exc: str = "", **detail) -> ErrorInfo:
        info = ErrorInfo(
            category=category, signature=error_signature(category, message),
            message=message or CATEGORY_LABELS[category], exception=exc,
            accepted=bool(gate["passed"]),
            retryable=CATEGORY_ATTEMPT_CAP.get(category, 1) > 0,
            detail={"exit_code": exit_code, "timed_out": bool(execution.get("timed_out")),
                    "stdout_tail": stdout[-300:], **detail},
        )
        return info

    if gate["passed"]:
        return make(CATEGORY_UNKNOWN, "执行成功且通过验收", "")

    exc, exc_msg = last_exception(stderr)

    # ① 基础设施层：不该重试的（Docker 缺失 / 守护进程不可用）
    #    结构化标记优先；拿不到标记时再看 stderr（历史运行、其他调用方可能不带该字段）
    from backend.agent.sandbox import is_infra_failure

    if execution.get("failure_kind") == "sandbox_unavailable" or is_infra_failure(stderr):
        return make(CATEGORY_ENVIRONMENT, stderr.strip() or "沙箱环境不可用", exc)

    # ② 超时（沙箱结构化标记优先，其次看 stderr 文案）
    if execution.get("timed_out") or any(m in lower for m in _PANDAS_MARKERS[CATEGORY_TIMEOUT]):
        return make(CATEGORY_TIMEOUT, exc_msg or "执行超时", exc)

    # ③ 资源超限（OOM）：Docker 137 = 被 SIGKILL，通常是内存上限
    if exit_code == 137 or any(m in lower for m in _PANDAS_MARKERS[CATEGORY_RESOURCE]):
        return make(CATEGORY_RESOURCE, exc_msg or "资源超限（疑似内存）", exc,
                    signal="SIGKILL" if exit_code == 137 else "")

    if ok:
        # ④ 代码跑通但验收不过：没有可用产物 → 结果为空
        return make(CATEGORY_EMPTY,
                    "执行成功但未产出可用结果（无结论 / 结果表为空）", exc,
                    failed_checks=list(gate.get("failed") or []))

    # ⑤ 回传契约优先于异常类型：错误发生在 dahelper 回传环节（如 int64 不可序列化）时，
    #    要修的是"怎么回传"，而不是"怎么聚合"。
    if any(m in lower for m in _PANDAS_MARKERS[CATEGORY_CONTRACT]):
        return make(CATEGORY_CONTRACT, exc_msg or exc or stderr, exc)

    # ⑥ 异常类型优先，其次看报错正文里的 pandas / numpy 特征串
    if exc in _EXC_TO_CATEGORY:
        return make(_EXC_TO_CATEGORY[exc], exc_msg or exc, exc)
    for category in _MARKER_ORDER:
        if any(m in lower for m in _PANDAS_MARKERS[category]):
            return make(category, exc_msg or exc or stderr, exc)

    # ⑦ 没有 traceback 但执行确实失败了（例如进程被外部终止、编码错误）
    tail = (stderr or stdout).strip()[-300:]
    return make(CATEGORY_UNKNOWN, tail or "执行失败，原因未识别", exc)


# ---------------------------------------------------------------------------
# 定向修复策略
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairStrategy:
    """一次修复的处方：策略标识 + 面向该错误类型的提示词块。"""

    category: str
    strategy_id: str
    focus: str
    hint: str
    retryable: bool = True
    max_attempts: int = 3

    def to_dict(self) -> dict:
        return {"category": self.category, "label": CATEGORY_LABELS.get(self.category, ""),
                "strategy_id": self.strategy_id, "focus": self.focus,
                "retryable": self.retryable, "max_attempts": self.max_attempts}


SCHEMA_HINT = (
    "【定向修复 · 列名映射】错误是「列 / 键不存在」。请只做列名对齐，不要改变分析口径："
    "① 先看上面「数据概况」里逐个列出的真实列名（注意大小写、前后空格、全角字符）；"
    "② 对照「行业语义层」中该指标的 `field`，确认口径字段到底叫什么；"
    "③ 统一用 `df[\"真实列名\"]` 取值，不要用 `df.列名` 属性写法，也不要臆造列名；"
    "④ 需要改动时先 `print(list(df.columns))` 自证列名，再做聚合。"
    "若某指标依赖的列确实不在数据里，改用语义层中字段存在的等价口径，并在结论里说明。"
)

DTYPE_HINT = (
    "【定向修复 · 类型与转换】错误是「类型 / 取值不合法」。请只修类型链路："
    "① 数值列被当字符串时先 `pd.to_numeric(errors=\"coerce\")`；"
    "② 时间列先 `pd.to_datetime(errors=\"coerce\")` 再取年月 / 分箱；"
    "③ 除法 / 占比先确认分母非 0，缺失值用 `fillna(0)` 或 `dropna()` 显式处理；"
    "④ 比较与分组前统一 dtype（`astype`），避免字符串与数字混排；"
    "⑤ 聚合函数与列类型要匹配（`sum` 不能作用于纯字符串列）。"
    "不要改动筛选范围与指标定义。"
)

EMPTY_HINT = (
    "【定向修复 · 空结果】代码执行成功但没有产出可用结果（结论为空 / 结果表 0 行）。"
    "请只排查「为什么没数据」：① 打印时间列 min/max，确认筛选窗口落在数据范围内；"
    "② 逐项核对过滤条件与数据里的实际取值（先 `value_counts()`，注意空格 / 全半角 / 大小写）；"
    "③ 检查 groupby / dropna 是否把样本全部丢掉；④ 放宽过滤条件或修正字段取值后重新计算；"
    "⑤ 无论结果多少，最后必须调用一次 `dahelper.save_text(\"结论\")`，并至少保存一张结果表或图表。"
)

SYNTAX_HINT = (
    "【定向修复 · 代码结构】错误是语法 / 缩进问题。**只改代码结构与书写形式**，"
    "不要改动指标、列名、筛选条件等任何分析逻辑：① 检查括号 / 引号是否闭合；"
    "② 检查中文全角标点（，：（）“”）被误当成 Python 语法符号；③ 检查缩进层级与缺失的 `:`；"
    "④ 检查 f-string 与三引号是否配对。修复后按原格式输出完整可运行代码。"
)

NAME_HINT = (
    "【定向修复 · 名称与符号】错误是「名字 / 属性不存在」。"
    "① 只用已导入的模块（pandas / numpy / matplotlib / dahelper）与已定义的变量；"
    "② 检查是否漏了 `import`、变量名拼写不一致、在赋值前使用；"
    "③ 检查是否把 DataFrame 当 dict 用（应 `df[\"...\"]` / `df.loc[...]`）；"
    "④ 不要引入沙箱中没有的库。除名称外，分析口径保持不变。"
)

TIMEOUT_HINT = (
    "【定向修复 · 降低计算量】上一次执行超时。"
    "① 只读必要列（`usecols`）与必要行；② 先过滤再聚合，避免全量 join / 排序；"
    "③ 用向量化与 groupby 取代 Python 逐行循环；④ 结果表限制 `head(N)`；"
    "⑤ 绘图一律基于聚合后的小表。分析口径不变，只降计算量与耗时。"
)

RESOURCE_HINT = (
    "【定向修复 · 降低内存】上一次执行因资源超限被终止（疑似 OOM）。"
    "① 只读必要列并指定 dtype（category / 小整数）；② 分块读取或先聚合再拼接；"
    "③ 避免一次性物化大量中间结果（反复 copy / concat）；④ 及时 `del` 大对象；"
    "⑤ 绘图前先聚合到小表。分析口径不变。"
)

CONTRACT_HINT = (
    "【定向修复 · 结果回传契约】dahelper 调用不正确。请严格照契约回传："
    "`import dahelper`；结果表 `dahelper.save_table(\"表名\", df.to_dict(\"records\"))`（≤100 行）；"
    "图表 `dahelper.save_chart(fig, \"图表名\")`；**最后必须且只调用一次 `dahelper.save_text(\"结论\")`**。"
    "传入的对象必须 JSON 可序列化：numpy 标量用 `.item()`、时间用 `str()` 或 ISO 字符串、"
    "不要把 DataFrame / Series 对象本身传进去。"
)

UNKNOWN_HINT = (
    "【定向修复 · 通用诊断】按 traceback 最后一帧定位到**出错的那一行**，只改与报错直接相关的代码，"
    "不要重写整段逻辑。对照「数据概况」核对列名与 dtype，对照语义层核对口径。"
    "如果一时看不出来，就用最小可运行片段自证（读数据 → 打印 shape 与 columns → 单步聚合），再补齐完整分析。"
)

ENVIRONMENT_HINT = (
    "沙箱运行环境不可用（例如 Docker 未启动），重新生成代码无法解决；"
    "本次跳过自修复，直接给出结构化失败说明。"
)

# 冻结的 V1 提示（旧实现里 FIX_USER_TMPL 的收尾指令，逐字保留）——Baseline 用
LEGACY_UNIFORM_HINT = (
    "请分析错误原因，输出修复后的完整代码（格式要求不变：计划 + ```python 代码块）。"
    "若错误与列名有关（如 KeyError），请对照「数据概况」与语义层检查列名的拼写、大小写与前后空格。"
)

STRATEGIES: dict[str, RepairStrategy] = {
    CATEGORY_COLUMN: RepairStrategy(CATEGORY_COLUMN, "schema_alignment", "对齐真实 schema 与语义层字段",
                                    SCHEMA_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_COLUMN]),
    CATEGORY_TYPE: RepairStrategy(CATEGORY_TYPE, "dtype_conversion", "修正 dtype / 转换 / 聚合",
                                  DTYPE_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_TYPE]),
    CATEGORY_EMPTY: RepairStrategy(CATEGORY_EMPTY, "filter_and_scope", "排查过滤条件 / 时间范围 / 字段取值",
                                   EMPTY_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_EMPTY]),
    CATEGORY_SYNTAX: RepairStrategy(CATEGORY_SYNTAX, "code_structure", "仅修代码结构，不动口径",
                                    SYNTAX_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_SYNTAX]),
    CATEGORY_NAME: RepairStrategy(CATEGORY_NAME, "symbol_binding", "修正名称 / 属性 / import",
                                  NAME_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_NAME]),
    CATEGORY_TIMEOUT: RepairStrategy(CATEGORY_TIMEOUT, "bounded_compute", "消除无界计算，降低数据规模",
                                     TIMEOUT_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_TIMEOUT]),
    CATEGORY_RESOURCE: RepairStrategy(CATEGORY_RESOURCE, "memory_bound", "降低内存占用",
                                      RESOURCE_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_RESOURCE]),
    CATEGORY_CONTRACT: RepairStrategy(CATEGORY_CONTRACT, "result_contract", "修正 dahelper 回传契约",
                                      CONTRACT_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_CONTRACT]),
    CATEGORY_UNKNOWN: RepairStrategy(CATEGORY_UNKNOWN, "general_diagnosis", "通用诊断",
                                     UNKNOWN_HINT, True, CATEGORY_ATTEMPT_CAP[CATEGORY_UNKNOWN]),
    CATEGORY_ENVIRONMENT: RepairStrategy(CATEGORY_ENVIRONMENT, "not_repairable", "环境不可用，不重试",
                                         ENVIRONMENT_HINT, False, 0),
}

# 冻结的 V1 处方（无差别重试时用的统一提示）
LEGACY_STRATEGY = RepairStrategy(CATEGORY_UNKNOWN, "uniform_legacy", "统一重试（V1 基线）",
                                 LEGACY_UNIFORM_HINT, True, 99)


def strategy_for(category: str) -> RepairStrategy:
    """类别 → 定向修复处方（未知类别落到通用诊断）。"""
    return STRATEGIES.get(category, STRATEGIES[CATEGORY_UNKNOWN])


# ---------------------------------------------------------------------------
# 有界重试决策
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairPolicy:
    """重试策略。`v2` = 分类 + 定向修复 + 复读检测；`v1` = 冻结的无差别重试。"""

    name: str = "v2"
    max_fix_attempts: int = 3
    # 允许的「连续同类错误」次数：默认 1，即第二次同类错误视为无进展、提前终止
    repeat_limit: int = 1
    per_category_cap: bool = True

    @property
    def is_legacy(self) -> bool:
        return self.name == "v1"


@dataclass(frozen=True)
class RepairDecision:
    """一次 stop / repair 的判定结果（进 trace，回答「为什么修 / 为什么不修」）。"""

    action: str                 # "repair" | "stop"
    status: str                 # 见 STATUS_* 常量
    reason: str                 # 人类可读原因
    reason_code: str            # 机器可读原因码
    strategy: RepairStrategy | None = None
    repeat_kind: str = ""       # "" | "identical" | "equivalent"
    attempt: int = 0            # 这是第几次修复（全局序号，从 1 开始）
    category_attempt: int = 0   # 该错误类别的第几次修复
    exhausted: bool = False

    def to_dict(self) -> dict:
        return {
            "action": self.action, "status": self.status, "reason": self.reason,
            "reason_code": self.reason_code, "repeat_kind": self.repeat_kind,
            "attempt": self.attempt, "category_attempt": self.category_attempt,
            "exhausted": self.exhausted,
            "strategy": self.strategy.to_dict() if self.strategy else None,
        }


STATUS_NOT_NEEDED = "not_needed"            # 首次执行即通过验收
STATUS_REPAIRING = "repairing"              # 正在修复
STATUS_SUCCEEDED = "succeeded"              # 修复后通过验收
STATUS_EXHAUSTED = "exhausted"              # 达到 MAX_FIX_ATTEMPTS / 该类额度用尽
STATUS_REPEATED = "repeated_failure"        # 复读：相同或等价错误再次出现，提前终止
STATUS_NOT_REPAIRABLE = "not_repairable"    # 环境不可用等，重试没有意义


def decide_repair(info: ErrorInfo, previous_errors: list[dict] | None = None,
                  policy: RepairPolicy | None = None, attempts: int = 1) -> RepairDecision:
    """要不要修、用什么策略修、还是就此收手（确定性、零 token）。

    `attempts` = 已完成的执行次数（首次执行后为 1），沿用 `Run.attempts` 口径：
    允许的修复次数上限是 `policy.max_fix_attempts`，因此总执行次数 ≤ 1 + 上限。
    """
    policy = policy or RepairPolicy()
    errors = list(previous_errors or [])

    if info.accepted:
        return RepairDecision(action="stop", status=STATUS_NOT_NEEDED,
                              reason="首次执行即通过结果验收", reason_code="accepted")

    # 冻结的 V1：不分类、不复读检测，只要还有额度就无差别重试
    if policy.is_legacy:
        if attempts <= policy.max_fix_attempts:
            return RepairDecision(action="repair", status=STATUS_REPAIRING,
                                  reason=f"执行失败，直接重新生成"
                                         f"（V1 无差别重试，第 {attempts} 次）",
                                  reason_code="legacy_uniform_retry",
                                  strategy=LEGACY_STRATEGY, attempt=attempts)
        return RepairDecision(action="stop", status=STATUS_EXHAUSTED,
                              reason=f"已达重试上限 {policy.max_fix_attempts} 次（V1 无差别重试）",
                              reason_code="legacy_max_attempts",
                              strategy=LEGACY_STRATEGY, attempt=attempts, exhausted=True)

    strategy = strategy_for(info.category)

    if not strategy.retryable:
        return RepairDecision(action="stop", status=STATUS_NOT_REPAIRABLE,
                              reason=f"{info.label}：{info.message}，重试无法解决",
                              reason_code="not_retryable", strategy=strategy)

    # ① 复读检测：相同 fingerprint 直接放弃，等价（同类）错误视为无进展
    previous = errors[-1] if errors else None
    if previous:
        if previous.get("signature") == info.signature:
            return RepairDecision(action="stop", status=STATUS_REPEATED,
                                  reason=f"复读：与上一次完全相同的错误（{info.label}），提前终止",
                                  reason_code="identical_error", strategy=strategy,
                                  repeat_kind="identical", exhausted=True)
        if policy.repeat_limit and previous.get("category") == info.category:
            return RepairDecision(action="stop", status=STATUS_REPEATED,
                                  reason=f"复读：连续同类错误（{info.label}），"
                                         f"视为修复无进展，提前终止",
                                  reason_code="equivalent_error", strategy=strategy,
                                  repeat_kind="equivalent", exhausted=True)

    # ② 全局上限：与旧实现一致（attempts ≤ MAX_FIX_ATTEMPTS 才继续修）
    if attempts > policy.max_fix_attempts:
        return RepairDecision(action="stop", status=STATUS_EXHAUSTED,
                              reason=f"已达修复上限 MAX_FIX_ATTEMPTS={policy.max_fix_attempts}",
                              reason_code="max_attempts", strategy=strategy,
                              attempt=attempts, exhausted=True)

    # ③ 每类错误的独立额度
    used = sum(1 for e in errors if e.get("category") == info.category)
    if policy.per_category_cap and used >= strategy.max_attempts:
        return RepairDecision(action="stop", status=STATUS_EXHAUSTED,
                              reason=f"「{info.label}」修复额度已用尽"
                                     f"（{used}/{strategy.max_attempts}）",
                              reason_code="category_cap", strategy=strategy,
                              attempt=used, exhausted=True)

    return RepairDecision(action="repair", status=STATUS_REPAIRING,
                          reason=f"{info.label} → 定向修复：{strategy.focus}",
                          reason_code="targeted_repair", strategy=strategy,
                          attempt=len(errors) + 1, category_attempt=used + 1)


def summarize_errors(previous_errors: list[dict] | None) -> dict:
    """错误序列汇总（进 trace）：按类别计数 + 复读次数 + 指纹序列。"""
    errors = list(previous_errors or [])
    by_category: dict[str, int] = {}
    for err in errors:
        category = err.get("category", CATEGORY_UNKNOWN)
        by_category[category] = by_category.get(category, 0) + 1
    repeats = 0
    for prev, cur in zip(errors, errors[1:]):
        if prev.get("signature") == cur.get("signature"):
            repeats += 1
    return {
        "count": len(errors),
        "by_category": by_category,
        "repeat_count": repeats,
        "signatures": [e.get("signature", "") for e in errors],
    }


__all__ = [
    "CATEGORIES", "CATEGORY_ATTEMPT_CAP", "CATEGORY_LABELS", "CATEGORY_COLUMN",
    "CATEGORY_CONTRACT", "CATEGORY_EMPTY", "CATEGORY_ENVIRONMENT", "CATEGORY_NAME",
    "CATEGORY_RESOURCE", "CATEGORY_SYNTAX", "CATEGORY_TIMEOUT", "CATEGORY_TYPE",
    "CATEGORY_UNKNOWN", "ErrorInfo", "LEGACY_STRATEGY", "LEGACY_UNIFORM_HINT",
    "RepairDecision", "RepairPolicy", "RepairStrategy", "STATUS_EXHAUSTED",
    "STATUS_NOT_NEEDED", "STATUS_NOT_REPAIRABLE", "STATUS_REPEATED", "STATUS_REPAIRING",
    "STATUS_SUCCEEDED", "STRATEGIES", "classify_error", "decide_repair",
    "error_signature", "last_exception", "normalize_message", "strategy_for",
    "summarize_errors",
]
