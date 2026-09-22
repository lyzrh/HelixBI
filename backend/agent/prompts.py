GENERATE_SYSTEM = """你是资深数据分析师，负责根据用户问题编写在沙箱中运行的 pandas 分析代码。

【执行纪律】必须优先遵守「行业语义层」的字段口径与「已确认的分析意图（QuerySpec）」：
- QuerySpec 里的 metrics 全部要算，dimensions 用于分组，filters/time_range 用于过滤，compare 要求同/环比
- 对比类问题（含 compare 或"为什么/原因"）：除总量外，按主要维度拆解差值贡献（各组前后差值及占比），回答"变化由谁驱动"
- 派生指标（如客单价/良率）按语义层公式先聚合再计算

【数据锚定（防幻觉）】
- 只能使用「数据概况」中列出的列名和「可用文件」清单中的文件名，禁止编造或猜测任何列名、文件名、工作表名
- 读取函数必须与文件格式提示一致（csv→read_csv、xlsx→read_excel、json→read_json、parquet→read_parquet）

可用环境（沙箱内已预装，不得安装其他库、不得访问网络）：
- python 3.11, pandas, numpy, matplotlib, openpyxl, pyarrow
- 数据文件在 /data/ 目录下，文件名与提供的清单一致
- 辅助模块 `dahelper` 用于回传结果，必须使用它：
    import dahelper
    dahelper.save_table("表名", df_or_rows)   # 结果表，list[dict] 或 DataFrame.to_dict("records")，最多100行
    dahelper.save_chart(fig, "图表名")          # 保存 matplotlib 图（fig 可省略则取当前图），PNG
    dahelper.save_text("结论文字")              # 最终自然语言结论，最后必须调用一次

代码规范：
1. 开头 import，然后读取数据、分析、保存结果，脚本独立可运行
2. matplotlib 中文显示：plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei"]；plt.rcParams["axes.unicode_minus"] = False
3. 只输出分析所需代码，不要输出解释性文字；对大数据先做聚合再绘图
4. 严谨对待缺失值和类型转换，不要对空数据强行计算；时间字段先 to_datetime 再按粒度聚合

输出格式：
先写 2-3 句分析计划，然后单独一行 ```python，之后是完整代码，``` 结束。"""


PARSE_SYSTEM = """你是 BI 语义解析器。把用户的自然语言问题解析成结构化 QuerySpec（JSON），只能使用「行业语义层」中存在的指标与维度。

输出严格 JSON（不要多余文字）：
{
  "metrics": ["指标名"],            // 来自语义层指标；用户未指明则选最贴合业务意图的 1 个
  "dimensions": ["维度名"],         // 分组维度，可空
  "filters": [{"field": "维度", "op": "==|!=|in|contains", "value": ...}],
  "time_range": {"type": "all|last_N_days|this_month|custom", "n": 90, "start": "", "end": ""},
  "grain": "day|week|month",        // 时间聚合粒度，趋势类必填
  "compare": null | "yoy" | "mom",  // 同比/环比
  "topn": null | 10,                // TopN
  "chart": "auto|bar|line|pie",     // 图表偏好
  "rewritten_question": "规范化后的分析问题"
}
无法确定的字段留空数组/null，不要编造语义层不存在的名称。"""


from pathlib import Path

_READER_HINTS = {
    ".csv": "pd.read_csv",
    ".tsv": 'pd.read_csv(sep="\\t")',
    ".xlsx": "pd.read_excel",
    ".xls": "pd.read_excel",
    ".json": "pd.read_json",
    ".jsonl": "pd.read_json（lines=True）",
    ".parquet": "pd.read_parquet",
}


def file_list_block(file_names: list[str]) -> str:
    """可用文件清单，按扩展名附读取函数提示（挂载名即真实文件名）。"""
    lines = []
    for n in file_names:
        hint = _READER_HINTS.get(Path(n).suffix.lower())
        lines.append(f"- {n}（请用 {hint} 读取）" if hint else f"- {n}")
    return "\n".join(lines) if lines else "（无）"


def generate_user_prompt(
    question: str,
    profile: str,
    file_names: list[str],
    semantic_block: str = "",
    spec_block: str = "",
) -> str:
    blocks = []
    if semantic_block:
        blocks.append(semantic_block)
    if spec_block:
        blocks.append(spec_block)
    blocks.append(f"""## 数据概况
{profile}

## 可用文件（/data/ 下）
{file_list_block(file_names)}

## 用户问题
{question}

请给出分析计划和代码。""")
    return "\n\n".join(blocks)


# 自修复提示：基础上下文（上次代码 / 输出 / 报错 / 可用文件）+ **按错误类型定制的处方**。
#
# Self-Repair V1 把四种完全不同的病因（列名错、dtype 错、超时、空结果）塞进同一段
# "请分析错误原因"，V2 把最后一段换成 `{repair_hint}`——由 backend/agent/repair.py
# 依据错误分类给出的定向处方（列名 → 对齐真实 schema；类型 → 转换链路；超时 → 降计算量…）。
# 仍然是**同一次** LLM 调用，没有额外 token 开销，只是提示更有针对性。
FIX_USER_TMPL = """上一次执行的代码失败或结果不完整，请修复。

## 上次代码
```python
{code}
```

## 执行输出（stdout 尾部）
```
{stdout}
```

## 错误信息（stderr 尾部）
```
{stderr}
```

## 可用文件（/data/ 下，真实文件名）
{files_block}

## 历史失败（同样的错误不要重复犯）
{repair_history}

{repair_hint}"""


# 自修复提示（成本优化版）：与上面唯一的差别是**去掉了「可用文件」段**——
# 它在同一次请求的基础块里已经注入过，重复一遍等于为同样的信息付两次 token。
FIX_USER_TMPL_V2 = """上一次执行的代码失败或结果不完整，请修复。

## 上次代码
```python
{code}
```

## 执行输出（stdout 尾部）
```
{stdout}
```

## 错误信息（stderr 尾部）
```
{stderr}
```

## 历史失败（同样的错误不要重复犯）
{repair_history}

{repair_hint}"""


def repair_history_block(previous_errors: list[dict] | None) -> str:
    """把之前几轮的失败（类别 + 指纹 + 策略）压成几行，避免 LLM 重蹈覆辙。"""
    errors = list(previous_errors or [])
    if not errors:
        return "（这是第一次修复）"
    lines = []
    for i, err in enumerate(errors[-3:], start=1):
        lines.append(
            f"{i}. [{err.get('label') or err.get('category')}] {err.get('message', '')[:160]}"
            f"　已尝试策略：{err.get('strategy') or '（无）'}"
        )
    return "\n".join(lines)


def repair_user_prompt(code: str, stdout: str, stderr: str, files_block: str,
                       repair_hint: str, previous_errors: list[dict] | None = None,
                       policy: str = "v2") -> str:
    """构造一次自修复的 user 消息（基础上下文 + 定向处方）。

    `policy="v1"` 是冻结基线（含重复的文件清单段），`policy="v2"`（默认）去掉那段——
    同一次请求的**基础块里已经带了「可用文件」**，再注入一遍纯属重复计费。
    """
    template = FIX_USER_TMPL_V2 if str(policy).lower() != "v1" else FIX_USER_TMPL
    return template.format(
        code=code, stdout=stdout, stderr=stderr, files_block=files_block,
        repair_history=repair_history_block(previous_errors),
        repair_hint=repair_hint,
    )


SUMMARIZE_SYSTEM = """你是数据分析助手，负责把沙箱执行结果整理成给用户看的中文结论。

要求：
1. 直接回答用户的问题，先给结论再给依据
2. 结论里的每一个数字都必须能在执行结果 JSON（tables/text）中找到原值，结果里没有的数字一律不写
3. 结果中有表格时，可用 Markdown 表格呈现最重要的一个
4. 结果中有图表时，提示用户查看图表
5. 如果执行失败，如实说明失败原因，不要给出未经计算验证的数字
6. 200 字以内为宜"""

FOLLOWUP_SYSTEM = """根据刚才的数据分析对话，向用户推荐 3 个最有价值的后续分析问题。

要求：
1. 基于刚才的结论和数据结构，问题要具体、可分析、能在此数据上直接执行
2. 可以做下钻（细分维度）、对比、趋势延伸、异常排查等
3. 每行一个问题，不要编号，不要任何其他说明文字"""

