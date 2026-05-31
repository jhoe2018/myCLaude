"""从 MCP MySQL 查询结果中提取用户邮箱列表。

从 stdin 读取 JSON 格式的 MCP 查询结果，解析出邮箱地址，
输出清洗后的收件人列表。

用法:
    echo '<MCP查询结果JSON>' | python user_fetcher.py
    echo '<MCP查询结果JSON>' | python user_fetcher.py --column email --name-column name
"""

import argparse
import json
import re
import sys
from typing import Any

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def parse_mcp_result(raw: str) -> tuple[list[str], list[list]]:
    """解析 MCP MySQL 返回的 JSON 数据。

    支持格式:
      1. [headers, *rows] — 标准格式
      2. {"columns": [...], "rows": [...]} — 对象格式
      3. [{"col": val}, ...] — 字典列表格式
    """
    data = json.loads(raw)

    if isinstance(data, dict):
        headers = list(data.get("columns", data.get("headers", [])))
        rows = data.get("rows", data.get("data", []))
        if isinstance(rows[0], dict) if rows else False:
            rows = [[row.get(h, "") for h in headers] for row in rows]
        return headers, rows

    if isinstance(data, list):
        if not data:
            return [], []
        if isinstance(data[0], list):
            headers = [str(h) for h in data[0]]
            return headers, data[1:]
        if isinstance(data[0], dict):
            headers = list(data[0].keys())
            rows = [[row.get(h, "") for h in headers] for row in data]
            return headers, rows

    return [], []


def extract_emails(headers: list[str], rows: list[list],
                   email_col: str = "email",
                   name_col: str = "") -> list[dict[str, str]]:
    """从查询结果中提取邮箱列表。

    Returns:
        [{"email": "a@x.com", "name": "张三"}, ...]
    """
    if not headers or not rows:
        return []

    headers_lower = [h.lower() for h in headers]
    email_idx = _find_column(headers, headers_lower, email_col)
    name_idx = _find_column(headers, headers_lower, name_col) if name_col else -1

    if email_idx < 0:
        print(f"错误: 未找到邮箱列，可用列: {headers}", file=sys.stderr)
        return []

    seen = set()
    recipients = []
    for row in rows:
        email = str(row[email_idx]).strip() if email_idx < len(row) else ""
        if not email or not EMAIL_RE.match(email):
            continue
        if email.lower() in seen:
            continue
        seen.add(email.lower())

        name = ""
        if name_idx >= 0 and name_idx < len(row):
            name = str(row[name_idx]).strip()

        recipients.append({"email": email, "name": name})

    return recipients


def _find_column(headers: list[str], headers_lower: list[str], target: str) -> int:
    """模糊匹配列名。

    匹配优先级:
      1. 完全匹配 (email == email)
      2. 包含匹配 (user_email 包含 email)
      3. 前缀匹配 (email_addr 以 email 开头)
    """
    t = target.lower()
    # 完全匹配
    for i, h in enumerate(headers_lower):
        if h == t:
            return i
    # 包含匹配
    for i, h in enumerate(headers_lower):
        if t in h:
            return i
    # 前缀匹配
    for i, h in enumerate(headers_lower):
        if h.startswith(t):
            return i
    return -1


def main():
    parser = argparse.ArgumentParser(description="从 MCP MySQL 结果中提取邮箱")
    parser.add_argument("--column", default="email", help="邮箱列名 (默认: email)")
    parser.add_argument("--name-column", default="", help="用户名列名")
    parser.add_argument("--output", choices=["json", "list", "count"],
                        default="json", help="输出格式")
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print("错误: stdin 无数据", file=sys.stderr)
        sys.exit(1)

    headers, rows = parse_mcp_result(raw)
    recipients = extract_emails(headers, rows, args.column, args.name_column)

    if args.output == "count":
        print(len(recipients))
    elif args.output == "list":
        for r in recipients:
            name_part = f" ({r['name']})" if r["name"] else ""
            print(f"{r['email']}{name_part}")
    else:
        print(json.dumps(recipients, ensure_ascii=False, indent=2))

    print(f"\n共提取 {len(recipients)} 个邮箱", file=sys.stderr)


if __name__ == "__main__":
    main()
