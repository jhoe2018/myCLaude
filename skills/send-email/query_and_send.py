#!/usr/bin/env python
"""一键查询 MySQL 并发送邮件 — 从 stdin 读 JSON 数据，生成 HTML 表格后发送。

用法:
    echo '[["col1","col2"],["val1","val2"]]' | python query_and_send.py --to xx@qq.com --subject "报表"
    mysql -e "SELECT * FROM users" | python query_and_send.py --to xx@qq.com --subject "用户数据"
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent


def read_data() -> tuple[list[str], list[list]]:
    """从 stdin 读取 JSON 格式的查询结果。

    JSON 格式: [headers, *rows] 即第一行为列名，后续行为数据。
    """
    raw = sys.stdin.read().strip()
    if not raw:
        print("错误: stdin 无数据", file=sys.stderr)
        sys.exit(1)

    data = json.loads(raw)

    if not data:
        print("错误: 查询结果为空", file=sys.stderr)
        sys.exit(1)

    headers = [str(h) for h in data[0]]
    rows = data[1:]
    return headers, rows


def send(headers: list[str], rows: list[list], to: str, subject: str,
         cc: str = "", smtp_args: list[str] | None = None):
    """生成 HTML 并调用 send_mail.py 发送。"""
    from table_builder import build_email

    html = build_email(subject, headers, rows,
                       template_path=str(SKILL_DIR / "email_template.html"))

    tmp_file = Path(f"/tmp/email_{subprocess.getpid()}.html")
    tmp_file.write_text(html, encoding="utf-8")

    cmd = [
        sys.executable, str(SKILL_DIR / "send_mail.py"),
        "--to", to,
        "--subject", subject,
        "--body-file", str(tmp_file),
        "--html",
    ]
    if cc:
        cmd += ["--cc", cc]
    if smtp_args:
        cmd += smtp_args

    result = subprocess.run(cmd, capture_output=True, text=True)
    tmp_file.unlink(missing_ok=True)

    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        sys.exit(result.returncode)
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description="查询 MySQL 并发送邮件")
    parser.add_argument("--to", required=True, help="收件人，多个用逗号分隔")
    parser.add_argument("--subject", required=True, help="邮件主题")
    parser.add_argument("--cc", default="", help="抄送")
    parser.add_argument("--smtp-args", nargs="*", default=[],
                        help="传递给 send_mail.py 的额外 SMTP 参数")
    args = parser.parse_args()

    headers, rows = read_data()
    send(headers, rows, args.to, args.subject, args.cc,
         list(args.smtp_args) if args.smtp_args else None)


if __name__ == "__main__":
    main()
