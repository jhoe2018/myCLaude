"""邮件发送工具 — 支持 HTML/纯文本，SMTP 配置通过环境变量或 JSON 文件读取。

用法:
    python send_mail.py --to 收件人 --subject 主题 --body 正文
    python send_mail.py --to 收件人 --subject 主题 --body-file 正文.html
    python send_mail.py --to a@x.com,b@x.com --subject 主题 --body 正文 --cc cc@x.com

SMTP 配置优先级: 命令行参数 > 环境变量 > 配置文件
"""

import argparse
import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "mail_config.json"

# 常用邮箱 SMTP 预设
PRESETS = {
    "qq":       {"host": "smtp.qq.com",       "port": 465, "ssl": True},
    "163":      {"host": "smtp.163.com",      "port": 465, "ssl": True},
    "126":      {"host": "smtp.126.com",      "port": 465, "ssl": True},
    "gmail":    {"host": "smtp.gmail.com",    "port": 587, "ssl": False},
    "outlook":  {"host": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "exmail":   {"host": "smtp.exmail.qq.com",    "port": 465, "ssl": True},
    "sina":     {"host": "smtp.sina.com",     "port": 465, "ssl": True},
    "sohu":     {"host": "smtp.sohu.com",     "port": 465, "ssl": True},
}


def load_config():
    """从配置文件加载 SMTP 配置。"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def resolve_smtp(args: argparse.Namespace) -> dict:
    """解析 SMTP 配置（命令行 > 环境变量 > 配置文件 > 预设）。"""
    config = load_config()

    host = args.host or os.getenv("SMTP_HOST") or config.get("host", "")
    port = args.port or os.getenv("SMTP_PORT") or config.get("port", 0)
    user = args.user or os.getenv("SMTP_USER") or config.get("user", "")
    password = args.password or os.getenv("SMTP_PASS") or config.get("password", "")
    use_ssl = os.getenv("SMTP_SSL", "").lower() in ("1", "true", "yes") or config.get("ssl", True)

    # 尝试从预设匹配
    if not host and args.mail_type:
        preset = PRESETS.get(args.mail_type.lower())
        if preset:
            host = preset["host"]
            port = preset["port"]
            use_ssl = preset["ssl"]

    if not host and user:
        # 从发件人邮箱自动推断
        domain = user.split("@")[-1].split(".")[0].lower()
        preset = PRESETS.get(domain)
        if preset:
            host = preset["host"]
            port = port or preset["port"]
            use_ssl = preset["ssl"]

    port = int(port) if port else (587 if not use_ssl else 465)

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "ssl": use_ssl,
    }


def send_mail(to: str, subject: str, body: str, smtp: dict, cc: str = "", is_html: bool = False):
    """发送邮件。"""
    if not all([smtp["host"], smtp["user"], smtp["password"]]):
        print("错误: SMTP 配置不完整，请设置 host/user/password")
        print("方式1: 环境变量 SMTP_HOST / SMTP_USER / SMTP_PASS")
        print("方式2: 配置文件 mail_config.json")
        sys.exit(1)

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp["user"]
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    content_type = "html" if is_html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
    if cc:
        recipients += [addr.strip() for addr in cc.split(",") if addr.strip()]

    try:
        if smtp["ssl"]:
            server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=15)
        else:
            server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=15)
            server.starttls()

        server.login(smtp["user"], smtp["password"])
        server.sendmail(smtp["user"], recipients, msg.as_string())
        server.quit()
        print(f"邮件已发送至: {', '.join(recipients)}")
    except smtplib.SMTPAuthenticationError:
        print("错误: SMTP 认证失败，请检查邮箱账号和授权码")
        sys.exit(1)
    except smtplib.SMTPConnectError:
        print(f"错误: 无法连接 SMTP 服务器 {smtp['host']}:{smtp['port']}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: 发送失败 — {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="邮件发送工具")
    parser.add_argument("--to", required=True, help="收件人，多个用逗号分隔")
    parser.add_argument("--subject", required=True, help="邮件主题")
    parser.add_argument("--body", default="", help="邮件正文（纯文本或HTML）")
    parser.add_argument("--body-file", default="", help="从文件读取正文")
    parser.add_argument("--cc", default="", help="抄送，多个用逗号分隔")
    parser.add_argument("--html", action="store_true", help="以 HTML 格式发送")
    # SMTP 参数（可选，覆盖环境变量/配置）
    parser.add_argument("--host", default="", help="SMTP 服务器地址")
    parser.add_argument("--port", type=int, default=0, help="SMTP 端口")
    parser.add_argument("--user", default="", help="发件人邮箱")
    parser.add_argument("--password", default="", help="邮箱授权码")
    parser.add_argument("--mail-type", default="",
                        help=f"邮箱类型预设: {', '.join(PRESETS.keys())}")

    args = parser.parse_args()

    # 读取正文
    body = args.body
    if args.body_file:
        bf = Path(args.body_file)
        if not bf.exists():
            print(f"错误: 文件不存在 — {args.body_file}")
            sys.exit(1)
        body = bf.read_text(encoding="utf-8")

    if not body.strip():
        print("错误: 邮件正文不能为空")
        sys.exit(1)

    smtp = resolve_smtp(args)
    is_html = args.html or body.strip().startswith("<")  # 自动检测 HTML
    send_mail(args.to, args.subject, body, smtp, args.cc, is_html)


if __name__ == "__main__":
    main()
