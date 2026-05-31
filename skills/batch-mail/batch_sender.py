"""批量邮件发送 — 从 stdin 读收件人列表，逐封发送，支持速率控制和失败重试。

用法:
    echo '[{"email":"a@x.com","name":"张三"},...]' | python batch_sender.py --subject "通知" --body-file content.html

    # 搭配 user_fetcher.py 使用:
    echo '<MCP结果>' | python user_fetcher.py | python batch_sender.py --subject "活动通知" --body-file email.html
"""

import argparse
import json
import smtplib
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SKILL_DIR = Path(__file__).parent
SEND_MAIL_DIR = Path(__file__).parent.parent / "send-email"
SEND_MAIL_SCRIPT = SEND_MAIL_DIR / "send_mail.py"

# 发送状态
STATUS_OK = "ok"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"


class BatchSender:
    """批量邮件发送器。"""

    def __init__(self, smtp_config: dict, rate_per_min: int = 30,
                 retry: int = 2, dry_run: bool = False):
        self.smtp = smtp_config
        self.rate_per_min = rate_per_min
        self.delay = 60.0 / rate_per_min if rate_per_min > 0 else 0
        self.retry = retry
        self.dry_run = dry_run
        self.results: list[dict] = []

    def send_batch(self, recipients: list[dict], subject: str, body: str,
                   is_html: bool = True, personalized: bool = True) -> list[dict]:
        """逐封发送邮件。

        Args:
            recipients: [{"email": "...", "name": "..."}, ...]
            subject: 邮件主题，支持 {name} 占位符
            body: 邮件正文，支持 {name} / {email} 占位符
            is_html: 是否为 HTML 邮件
            personalized: 是否启用占位符替换

        Returns:
            [{"email": "...", "status": "ok|fail|skip", "error": "..."}, ...]
        """
        total = len(recipients)
        self.results = []
        start_time = time.time()

        print(f"批量发送开始 — 共 {total} 封，速率 {self.rate_per_min}/分钟",
              file=sys.stderr)
        if self.dry_run:
            print("*** 演习模式，不会实际发送 ***", file=sys.stderr)

        conn = self._connect() if not self.dry_run else None

        for i, r in enumerate(recipients):
            email = r.get("email", "").strip()
            name = r.get("name", "").strip()

            if not email:
                self.results.append({**r, "status": STATUS_SKIP, "error": "邮箱为空"})
                continue

            # 占位符替换
            subj = subject.replace("{name}", name).replace("{email}", email) if personalized else subject
            content = body
            if personalized:
                content = content.replace("{name}", name).replace("{email}", email)

            # 发送（带重试）
            status, error = self._send_one(conn, email, subj, content, is_html)

            result = {"email": email, "name": name, "status": status}
            if error:
                result["error"] = error
            self.results.append(result)

            # 进度提示
            progress = (i + 1) / total * 100
            icon = "✓" if status == STATUS_OK else "✗" if status == STATUS_FAIL else "⊘"
            print(f"  [{i+1}/{total}] {icon} {email} {f'— {error}' if error else ''}",
                  file=sys.stderr)

            # 速率控制
            if self.delay > 0 and i < total - 1 and status != STATUS_FAIL:
                time.sleep(self.delay)

        if conn:
            try:
                conn.quit()
            except Exception:
                pass

        elapsed = time.time() - start_time
        self._print_summary(elapsed)

        return self.results

    def _connect(self):
        """建立 SMTP 连接。"""
        try:
            if self.smtp.get("ssl", True):
                conn = smtplib.SMTP_SSL(self.smtp["host"], self.smtp["port"], timeout=15)
            else:
                conn = smtplib.SMTP(self.smtp["host"], self.smtp["port"], timeout=15)
                conn.starttls()
            conn.login(self.smtp["user"], self.smtp["password"])
            return conn
        except smtplib.SMTPAuthenticationError:
            print("错误: SMTP 认证失败，请检查邮箱和授权码", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"错误: SMTP 连接失败 — {e}", file=sys.stderr)
            sys.exit(1)

    def _send_one(self, conn, to: str, subject: str, body: str,
                  is_html: bool) -> tuple[str, str]:
        """发送单封邮件，返回 (status, error_message)。"""
        if self.dry_run:
            return STATUS_OK, ""

        last_error = ""
        for attempt in range(self.retry + 1):
            try:
                self._do_send(conn, to, subject, body, is_html)
                return STATUS_OK, ""
            except smtplib.SMTPRecipientsRefused as e:
                return STATUS_FAIL, f"收件被拒: {e}"
            except smtplib.SMTPSenderRefused as e:
                return STATUS_FAIL, f"发件被拒: {e}"
            except smtplib.SMTPDataError as e:
                return STATUS_FAIL, f"数据错误: {e}"
            except Exception as e:
                last_error = str(e)
                if attempt < self.retry:
                    time.sleep(2 * (attempt + 1))
                    # 重连
                    try:
                        conn = self._connect()
                    except Exception:
                        pass

        return STATUS_FAIL, last_error

    def _do_send(self, conn, to: str, subject: str, body: str, is_html: bool):
        """构造并发送 MIME 邮件。"""
        msg = MIMEMultipart("alternative")
        msg["From"] = self.smtp["user"]
        msg["To"] = to
        msg["Subject"] = subject
        content_type = "html" if is_html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        if conn:
            conn.sendmail(self.smtp["user"], [to], msg.as_string())

    def _print_summary(self, elapsed: float):
        ok = sum(1 for r in self.results if r["status"] == STATUS_OK)
        fail = sum(1 for r in self.results if r["status"] == STATUS_FAIL)
        skip = sum(1 for r in self.results if r["status"] == STATUS_SKIP)

        print(f"\n发送完成 — 成功 {ok} / 失败 {fail} / 跳过 {skip} / "
              f"耗时 {elapsed:.0f}s", file=sys.stderr)

    def save_report(self, path: str = ""):
        """保存发送报告为 JSON。"""
        if not path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"/tmp/batch_mail_report_{ts}.json"
        report = {
            "time": datetime.now().isoformat(),
            "total": len(self.results),
            "ok": sum(1 for r in self.results if r["status"] == STATUS_OK),
            "fail": sum(1 for r in self.results if r["status"] == STATUS_FAIL),
            "skip": sum(1 for r in self.results if r["status"] == STATUS_SKIP),
            "results": self.results,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"发送报告: {path}", file=sys.stderr)
        return path


def load_smtp_config(mail_type: str = "", host: str = "", port: int = 0,
                     user: str = "", password: str = "") -> dict:
    """加载 SMTP 配置 — 参数 > 环境变量 > 配置文件。"""
    import os

    PRESETS = {
        "qq":      {"host": "smtp.qq.com",       "port": 465, "ssl": True},
        "163":     {"host": "smtp.163.com",      "port": 465, "ssl": True},
        "126":     {"host": "smtp.126.com",      "port": 465, "ssl": True},
        "gmail":   {"host": "smtp.gmail.com",    "port": 587, "ssl": False},
        "outlook": {"host": "smtp-mail.outlook.com", "port": 587, "ssl": False},
        "exmail":  {"host": "smtp.exmail.qq.com","port": 465, "ssl": True},
    }

    # 优先命令行
    if host and user and password:
        return {"host": host, "port": port or 465, "user": user,
                "password": password, "ssl": True}

    # 预设
    if mail_type and mail_type in PRESETS:
        preset = PRESETS[mail_type]
        return {**preset, "user": user or os.getenv("SMTP_USER", ""),
                "password": password or os.getenv("SMTP_PASS", "")}

    # 环境变量
    h = host or os.getenv("SMTP_HOST", "")
    p = port or int(os.getenv("SMTP_PORT", "465"))
    u = user or os.getenv("SMTP_USER", "")
    pw = password or os.getenv("SMTP_PASS", "")
    if h and u and pw:
        return {"host": h, "port": p, "user": u, "password": pw, "ssl": True}

    # 配置文件
    config_file = SEND_MAIL_DIR / "mail_config.json"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            if cfg.get("host") and cfg.get("user") and cfg.get("password"):
                return cfg

    print("错误: SMTP 配置不完整", file=sys.stderr)
    print("请设置: SMTP_HOST/SMTP_USER/SMTP_PASS 环境变量或 mail_config.json",
          file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="批量邮件发送")
    parser.add_argument("--subject", required=True, help="邮件主题，支持 {name}/{email} 占位")
    parser.add_argument("--body", default="", help="邮件正文")
    parser.add_argument("--body-file", default="", help="从文件读取正文")
    parser.add_argument("--rate", type=int, default=30, help="每分钟发送速率 (默认: 30)")
    parser.add_argument("--retry", type=int, default=2, help="失败重试次数 (默认: 2)")
    parser.add_argument("--dry-run", action="store_true", help="演习模式，不实际发送")
    parser.add_argument("--no-personalize", action="store_true", help="禁用占位符替换")
    parser.add_argument("--report", default="", help="发送报告输出路径")
    # SMTP
    parser.add_argument("--mail-type", default="", help="邮箱类型: qq/163/gmail/exmail")
    parser.add_argument("--host", default="", help="SMTP 地址")
    parser.add_argument("--port", type=int, default=0, help="SMTP 端口")
    parser.add_argument("--user", default="", help="发件人邮箱")
    parser.add_argument("--password", default="", help="授权码")

    args = parser.parse_args()

    # 读正文
    body = args.body
    if args.body_file:
        bf = Path(args.body_file)
        if not bf.exists():
            print(f"错误: 文件不存在 — {args.body_file}", file=sys.stderr)
            sys.exit(1)
        body = bf.read_text(encoding="utf-8")
    if not body.strip():
        print("错误: 邮件正文不能为空", file=sys.stderr)
        sys.exit(1)

    # 读收件人列表
    raw = sys.stdin.read().strip()
    if not raw:
        print("错误: stdin 无收件人数据", file=sys.stderr)
        sys.exit(1)

    recipients = json.loads(raw)
    if not isinstance(recipients, list):
        recipients = [recipients]
    if not recipients:
        print("错误: 收件人列表为空", file=sys.stderr)
        sys.exit(1)

    # SMTP 配置
    smtp = load_smtp_config(args.mail_type, args.host, args.port,
                            args.user, args.password)

    # 发送
    is_html = body.strip().startswith("<") or args.body_file.endswith(".html")
    sender = BatchSender(smtp, args.rate, args.retry, args.dry_run)
    results = sender.send_batch(
        recipients=recipients,
        subject=args.subject,
        body=body,
        is_html=is_html,
        personalized=not args.no_personalize,
    )

    # 报告
    report_path = sender.save_report(args.report or "")
    print(report_path)


if __name__ == "__main__":
    main()
