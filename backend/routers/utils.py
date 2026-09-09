"""通用工具路由：前端数据表导出 Excel。"""

import io
import urllib.parse

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import Response

from backend.schemas import ExportTableBody

router = APIRouter(prefix="/utils")


@router.post("/export_table")
def export_table(body: ExportTableBody):
    """把前端持有的结果表（rows）导出为多 Sheet xlsx。"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet in body.sheets[:8]:
            name = sheet.name.strip()[:28] or f"Sheet{len(sheet.name)}"
            df = pd.DataFrame(sheet.rows)
            # Excel 不允许的字符（控制符）会导致 openpyxl 报错，统一清洗
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].map(
                        lambda v: "".join(ch for ch in str(v) if ord(ch) >= 32)
                        if isinstance(v, str) else v)
            df.to_excel(writer, sheet_name=name, index=False)
    filename = urllib.parse.quote(f"{body.filename.strip() or '导出数据'}.xlsx")
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
