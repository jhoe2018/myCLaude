"""查询结果转 HTML 表格 — 将 MySQL MCP 返回的数据转为邮件可用的 HTML。"""

from datetime import datetime


def build_table(headers: list[str], rows: list[list]) -> str:
    """将列名和数据行转为 HTML <table>。

    Args:
        headers: 列名列表
        rows: 数据行列表，每行为一个 list

    Returns:
        HTML <table> 字符串
    """
    parts = ["<table>"]

    # 表头
    parts.append("<thead><tr>")
    parts.append("<th>#</th>")
    for h in headers:
        parts.append(f"<th>{_esc(h)}</th>")
    parts.append("</tr></thead>")

    # 表体
    parts.append("<tbody>")
    for i, row in enumerate(rows, 1):
        parts.append("<tr>")
        parts.append(f"<td class=\"num\">{i}</td>")
        for cell in row:
            td = _format_cell(cell)
            parts.append(td)
        parts.append("</tr>")
    parts.append("</tbody>")
    parts.append("</table>")

    return "\n".join(parts)


def build_email(title: str, headers: list[str], rows: list[list],
                template_path: str = "") -> str:
    """生成完整 HTML 邮件正文。

    Args:
        title: 报表标题
        headers: 列名列表
        rows: 数据行列表
        template_path: 模板文件路径，省略则用内联模板

    Returns:
        完整 HTML 字符串
    """
    table_html = build_table(headers, rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if template_path:
        with open(template_path, "r", encoding="utf-8") as f:
            tpl = f.read()
    else:
        from pathlib import Path
        default_tpl = Path(__file__).parent / "email_template.html"
        if default_tpl.exists():
            tpl = default_tpl.read_text(encoding="utf-8")
        else:
            tpl = _default_template()

    return tpl.format(
        title=_esc(title),
        datetime=now,
        row_count=len(rows),
        table=table_html,
    )


def _format_cell(value) -> str:
    """格式化单个单元格。"""
    if value is None:
        return '<td class="null">-</td>'

    s = str(value).strip()
    if not s:
        return '<td class="null">-</td>'

    # 数字右对齐
    try:
        float(s.replace(",", ""))
        return f'<td class="num">{_esc(s)}</td>'
    except (ValueError, AttributeError):
        pass

    return f'<td class="txt">{_esc(s)}</td>'


def _esc(text: str) -> str:
    """HTML 实体转义。"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _default_template() -> str:
    return """<html>
<head><meta charset="utf-8"><style>
  table {{ border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; }}
  th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
  td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  .header {{ color: #666; font-size: 13px; margin-bottom: 15px; }}
  .footer {{ color: #999; font-size: 12px; margin-top: 20px; border-top: 1px solid #eee; padding-top: 10px; }}
  .null {{ color: #aaa; font-style: italic; }}
  .num {{ text-align: right; }}
  .txt {{ text-align: left; }}
</style></head>
<body>
  <h3>{title}</h3>
  <div class="header">{datetime} | {row_count} 行</div>
  {table}
  <div class="footer">此邮件由 Claude Code send-email 技能自动生成</div>
</body>
</html>"""
