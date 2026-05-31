"""公共邮件发送模块 — send-email / batch-mail 两个 Skill 共享。

提供统一的 SMTP 配置加载、邮件发送、HTML 表格生成，
避免跨 Skill 重复代码。
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

# ── 常量 ───────────────────────────────────────────

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

PRESETS: dict[str, dict] = {
    "qq":      {"host": "smtp.qq.com",        "port": 465, "ssl": True},
    "163":     {"host": "smtp.163.com",       "port": 465, "ssl": True},
    "126":     {"host": "smtp.126.com",       "port": 465, "ssl": True},
    "gmail":   {"host": "smtp.gmail.com",     "port": 587, "ssl": False},
    "outlook": {"host": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "exmail":  {"host": "smtp.exmail.qq.com", "port": 465, "ssl": True},
    "sina":    {"host": "smtp.sina.com",      "port": 465, "ssl": True},
}

DEFAULT_HTML_TEMPLATE = """<html>
<head><meta charset="utf-8"><style>
  table { border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; }
  th { background: #2c3e50; color: white; padding: 10px; text-align: left; }
  td { padding: 8px; border-bottom: 1px solid #ddd; }
  tr:nth-child(even) { background: #f8f9fa; }
  tr:hover { background: #e9ecef; }
  .header { color: #666; font-size: 13px; margin-bottom: 15px; }
  .footer { color: #999; font-size: 12px; margin-top: 20px; border-top: 1px solid #eee; padding-top: 10px; }
  .null { color: #aaa; font-style: italic; }
  .num { text-align: right; }
  .txt { text-align: left; }
  .ok { color: #27ae60; }
  .fail { color: #e74c3c; }
</style></head>
<body>
  <h3>{title}</h3>
  <div class="header">{datetime} | {row_count} 行</div>
  {table}
  <div class="footer">{footer}</div>
</body>
</html>"""

# ── SMTP 配置加载 ──────────────────────────────────

def load_smtp_config(mail_type: str = "", host: str = "", port: int = 0,
                     user: str = "", password: str = "",
                     config_paths: list[str] | None = None) -> dict:
    """统一加载 SMTP 配置。

    优先级: 命令行参数 > 环境变量 > 配置文件 > 预设

    Args:
        mail_type: 预设类型 (qq/163/gmail/...)
        host/port/user/password: 命令行覆盖
        config_paths: 额外的配置文件搜索路径

    Returns:
        {"host", "port", "user", "password", "ssl"}
    """
    # 1. 命令行直接指定
    if host and user and password:
        return {"host": host, "port": port or 465, "user": user,
                "password": password, "ssl": True}

    # 2. 从 mail_type 预设
    preset = PRESETS.get(mail_type.lower()) if mail_type else None
    if preset:
        return {
            **preset,
            "user": user or os.getenv("SMTP_USER", ""),
            "password": password or os.getenv("SMTP_PASS", ""),
        }

    # 3. 环境变量
    h = host or os.getenv("SMTP_HOST", "")
    p = port or int(os.getenv("SMTP_PORT", "0"))
    u = user or os.getenv("SMTP_USER", "")
    pw = password or os.getenv("SMTP_PASS", "")
    if h and u and pw:
        ssl = os.getenv("SMTP_SSL", "true").lower() in ("1", "true", "yes")
        return {"host": h, "port": p or (465 if ssl else 587), "user": u,
                "password": pw, "ssl": ssl}

    # 4. 配置文件
    search_paths = config_paths or []
    search_paths.extend([
        str(Path(__file__).parent.parent / "send-email" / "mail_config.json"),
        str(Path(__file__).parent / "mail_config.json"),
    ])
    for cfg_path in search_paths:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("host") and cfg.get("user") and cfg.get("password"):
                return {
                    "host": cfg.get("host", ""),
                    "port": int(cfg.get("port", 465)),
                    "user": cfg.get("user", ""),
                    "password": cfg.get("password", ""),
                    "ssl": cfg.get("ssl", True),
                }

    raise RuntimeError(
        "SMTP 未配置。请设置环境变量 SMTP_HOST/SMTP_USER/SMTP_PASS "
        "或配置文件 mail_config.json"
    )


# ── 邮件发送 ───────────────────────────────────────

def send_one(to: str, subject: str, body: str, smtp: dict,
             cc: str = "", is_html: bool = False,
             timeout: int = 15) -> tuple[bool, str]:
    """发送单封邮件。

    Returns:
        (ok, message)
    """
    if not to.strip():
        return False, "收件人不能为空"

    # 自动检测 HTML
    if not is_html and body.strip().startswith("<"):
        is_html = True

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp["user"]
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    content_type = "html" if is_html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    if cc:
        recipients += [a.strip() for a in cc.split(",") if a.strip()]

    try:
        if smtp.get("ssl", True):
            server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=timeout)
        else:
            server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=timeout)
            server.starttls()
        server.login(smtp["user"], smtp["password"])
        server.sendmail(smtp["user"], recipients, msg.as_string())
        server.quit()
        return True, f"已发送至 {', '.join(recipients)}"
    except smtplib.SMTPAuthenticationError:
        return False, "认证失败，请检查授权码"
    except smtplib.SMTPConnectError:
        return False, f"无法连接 {smtp['host']}:{smtp['port']}"
    except Exception as e:
        return False, str(e)


def send_batch(recipients: list[dict], subject: str, body: str,
               smtp: dict, is_html: bool = True,
               rate_per_min: int = 30, retry: int = 2,
               personalized: bool = True,
               on_progress=None) -> list[dict]:
    """批量发送邮件，带速率控制和重试。

    Args:
        recipients: [{"email": "...", "name": "..."}, ...]
        subject: 主题，支持 {name}/{email} 占位
        body: 正文，支持 {name}/{email} 占位
        smtp: SMTP 配置
        is_html: 是否 HTML
        rate_per_min: 每分钟发送速率
        retry: 失败重试次数
        personalized: 是否替换占位符
        on_progress: 进度回调 fn(index, total, result)

    Returns:
        [{"email", "name", "status": "ok|fail|skip", "error": ""}, ...]
    """
    import time as _time

    total = len(recipients)
    delay = 60.0 / rate_per_min if rate_per_min > 0 else 0
    results: list[dict] = []

    for i, r in enumerate(recipients):
        email = r.get("email", "").strip()
        name = r.get("name", "").strip()

        if not email or not EMAIL_RE.match(email):
            results.append({**r, "status": "skip", "error": "邮箱无效"})
            if on_progress:
                on_progress(i + 1, total, results[-1])
            continue

        subj = subject.replace("{name}", name).replace("{email}", email) if personalized else subject
        content = body.replace("{name}", name).replace("{email}", email) if personalized else body

        ok, error = False, ""
        for attempt in range(retry + 1):
            ok, error = send_one(email, subj, content, smtp, is_html=is_html)
            if ok:
                break
            if attempt < retry:
                _time.sleep(2 * (attempt + 1))

        result = {"email": email, "name": name,
                  "status": "ok" if ok else "fail"}
        if error:
            result["error"] = error
        results.append(result)

        if on_progress:
            on_progress(i + 1, total, result)

        if delay > 0 and i < total - 1:
            _time.sleep(delay)

    return results


# ── HTML 表格生成 ──────────────────────────────────

def build_table(headers: list[str], rows: list[list]) -> str:
    """查询结果 → HTML <table>。"""
    parts = ["<table><thead><tr><th>#</th>"]
    for h in headers:
        parts.append(f"<th>{_esc(h)}</th>")
    parts.append("</tr></thead><tbody>")
    for i, row in enumerate(rows, 1):
        parts.append("<tr>")
        parts.append(f"<td class=\"num\">{i}</td>")
        for cell in row:
            parts.append(_format_cell(cell))
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def build_report(title: str, headers: list[str], rows: list[list],
                 template: str = "", footer: str = "此邮件由 Claude Code 自动生成") -> str:
    """生成完整 HTML 报表邮件。"""
    tpl = template or DEFAULT_HTML_TEMPLATE
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return tpl.format(
        title=_esc(title),
        datetime=now,
        row_count=len(rows),
        table=build_table(headers, rows),
        footer=_esc(footer),
    )


# ── 校验工具 ───────────────────────────────────────

def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email.strip()))


def extract_emails(data: Any, email_col: str = "email",
                   name_col: str = "") -> list[dict]:
    """从 MCP 查询结果中提取邮箱列表。

    支持多种格式:
      - [headers, *rows]
      - {"columns": [...], "rows": [...]}
      - [{"col": val}, ...]

    Returns:
        [{"email": "a@x.com", "name": "张三"}, ...]
    """
    # 解析
    if isinstance(data, str):
        data = json.loads(data)

    if isinstance(data, dict):
        headers = list(data.get("columns", data.get("headers", [])))
        rows = data.get("rows", data.get("data", []))
        if rows and isinstance(rows[0], dict):
            rows = [[row.get(h, "") for h in headers] for row in rows]
    elif isinstance(data, list) and data:
        if isinstance(data[0], list):
            headers = [str(h) for h in data[0]]
            rows = data[1:]
        elif isinstance(data[0], dict):
            headers = list(data[0].keys())
            rows = [[row.get(h, "") for h in headers] for row in data]
        else:
            return []
    else:
        return []

    if not headers or not rows:
        return []

    # 定位列
    headers_lower = [h.lower() for h in headers]
    e_idx = _find_col(headers, headers_lower, email_col)
    n_idx = _find_col(headers, headers_lower, name_col) if name_col else -1

    if e_idx < 0:
        print(f"错误: 未找到邮箱列，可用: {headers}", file=sys.stderr)
        return []

    seen = set()
    result = []
    for row in rows:
        email = str(row[e_idx]).strip() if e_idx < len(row) else ""
        if not email or not EMAIL_RE.match(email):
            continue
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        name = str(row[n_idx]).strip() if n_idx >= 0 and n_idx < len(row) else ""
        result.append({"email": email, "name": name})

    return result


# ── 内部工具 ───────────────────────────────────────

def _esc(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _format_cell(value) -> str:
    if value is None:
        return '<td class="null">-</td>'
    s = str(value).strip()
    if not s:
        return '<td class="null">-</td>'
    try:
        float(s.replace(",", ""))
        return f'<td class="num">{_esc(s)}</td>'
    except (ValueError, AttributeError):
        pass
    return f'<td class="txt">{_esc(s)}</td>'


def _find_col(headers: list[str], lower: list[str], target: str) -> int:
    t = target.lower()
    for i, h in enumerate(lower):
        if h == t:
            return i
    for i, h in enumerate(lower):
        if t in h:
            return i
    for i, h in enumerate(lower):
        if h.startswith(t):
            return i
    return -1
