"""user-messaging MCP Server v2 — 用户查询 + 邮件 + 短信 + 异步队列。

via stdio JSON-RPC 2.0，纯标准库 + pymysql，零 MCP SDK 依赖。

v2 特性:
  - 结构化日志，按天轮转，保留 30 天
  - Token 认证 (UM_AUTH_TOKEN)
  - 统一 JSON 响应格式 {"ok": bool, "data": ..., "error": "...", "rid": "..."}
  - Redis 异步队列 (批量邮件/短信不阻塞)
  - 健康检查 + 统计接口
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import smtplib
import sys
import time
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

# ════════════════════════════════════════════════════
#  配置 & 常量
# ════════════════════════════════════════════════════

SERVER_DIR = Path(__file__).parent
CONFIG_FILE = SERVER_DIR / "config.json"
LOG_DIR = SERVER_DIR / "logs"

VERSION = "2.0.0"
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "user-messaging"

AUTH_TOKEN = os.getenv("UM_AUTH_TOKEN", "")
QUEUE_MAX_SIZE = int(os.getenv("UM_QUEUE_MAX", "5000"))

# ── 日志 ───────────────────────────────────────────

LOG_DIR.mkdir(exist_ok=True)

_logger = logging.getLogger(SERVER_NAME)
_logger.setLevel(
    getattr(logging, os.getenv("UM_LOG_LEVEL", "INFO").upper(), logging.INFO)
)

_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
))
_logger.addHandler(_console)

_file = logging.handlers.TimedRotatingFileHandler(
    filename=str(LOG_DIR / "server.log"),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
_file.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] rid=%(rid)s %(message)s"
))
_logger.addHandler(_file)

log = _logger

# ── 统计 ───────────────────────────────────────────

_stats: dict[str, int] = {
    "requests_total": 0,
    "requests_ok": 0,
    "requests_err": 0,
    "emails_sent": 0,
    "emails_failed": 0,
    "sms_sent": 0,
    "sms_failed": 0,
    "queue_enqueued": 0,
    "queue_processed": 0,
    "queue_failed": 0,
}
_start_time = time.time()


def _uptime() -> float:
    return time.time() - _start_time


# ════════════════════════════════════════════════════
#  配置加载
# ════════════════════════════════════════════════════

_config: dict = {}


def _env_or(key: str, default: Any = "") -> Any:
    env_key = f"UM_{key.upper().replace('.', '_')}"
    if env_key in os.environ:
        return os.environ[env_key]
    val = _config
    for p in key.split("."):
        if isinstance(val, dict):
            val = val.get(p, default)
        else:
            return default
    return val if val is not None else default


def load_config() -> dict:
    global _config
    if CONFIG_FILE.exists():
        _config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    log.info("配置已加载")
    return _config


# ════════════════════════════════════════════════════
#  认证
# ════════════════════════════════════════════════════

def _check_auth(params: dict) -> str | None:
    if not AUTH_TOKEN:
        return None
    token = params.get("_token", "") or params.get("token", "")
    if token != AUTH_TOKEN:
        return "认证失败: token 无效"
    return None


# ════════════════════════════════════════════════════
#  Redis 异步队列 (可选)
# ════════════════════════════════════════════════════

def _get_redis():
    try:
        import redis as _redis
    except ImportError:
        log.warning("redis 包未安装，队列功能不可用")
        return None

    r_host = os.getenv("UM_REDIS_HOST", os.getenv("REDIS_HOST", "127.0.0.1"))
    r_port = int(os.getenv("UM_REDIS_PORT", os.getenv("REDIS_PORT", "6379")))
    r_pass = os.getenv("UM_REDIS_PASSWORD", os.getenv("REDIS_PASSWORD", ""))
    r_db = int(os.getenv("UM_REDIS_DB", "0"))

    try:
        r = _redis.Redis(
            host=r_host, port=r_port, password=r_pass or None,
            db=r_db, socket_connect_timeout=3, decode_responses=True,
        )
        r.ping()
        return r
    except Exception as e:
        log.warning(f"Redis 连接失败: {e}")
        return None


def enqueue_job(job_type: str, payload: dict) -> dict:
    r = _get_redis()
    if not r:
        return {"ok": False, "error": "Redis 不可用，无法入队"}

    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id": job_id, "type": job_type, "payload": payload,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    queue_key = f"um:queue:{job_type}"
    r.rpush(queue_key, json.dumps(job, ensure_ascii=False))
    r.hset(f"um:job:{job_id}", mapping=job)

    _stats["queue_enqueued"] += 1
    log.info(f"任务入队: {job_id} type={job_type}")
    return {"ok": True, "data": {"job_id": job_id}, "message": f"任务 {job_id} 已入队"}


def get_job_status(job_id: str) -> dict:
    r = _get_redis()
    if not r:
        return {"ok": False, "error": "Redis 不可用"}

    job = r.hgetall(f"um:job:{job_id}")
    if not job:
        return {"ok": False, "error": f"任务 {job_id} 不存在"}

    return {"ok": True, "data": dict(job)}


def process_queue(limit: int = 10) -> dict:
    r = _get_redis()
    if not r:
        return {"ok": False, "error": "Redis 不可用"}

    processed = []
    for job_type in ("send_email", "send_sms"):
        for _ in range(limit):
            raw = r.lpop(f"um:queue:{job_type}")
            if not raw:
                break
            job = json.loads(raw)
            job["status"] = "processing"
            r.hset(f"um:job:{job['job_id']}", mapping=job)

            try:
                if job_type == "send_email":
                    result = send_email(**job["payload"])
                else:
                    result = send_sms(**job["payload"])

                job["status"] = "done" if result.get("ok") else "failed"
                job["result"] = result
                job["processed_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)

            r.hset(f"um:job:{job['job_id']}", mapping=job)
            processed.append(job)
            _stats["queue_processed" if job["status"] == "done" else "queue_failed"] += 1

    return {"ok": True, "data": {"processed": len(processed), "jobs": processed}}


# ════════════════════════════════════════════════════
#  MySQL
# ════════════════════════════════════════════════════

def _get_mysql_conn():
    import pymysql
    return pymysql.connect(
        host=_env_or("mysql.host", "127.0.0.1"),
        port=int(_env_or("mysql.port", "3306")),
        user=_env_or("mysql.user", "root"),
        password=_env_or("mysql.password", ""),
        database=_env_or("mysql.database", ""),
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=30,
    )


def get_users(filters: dict | None = None, **kwargs) -> dict:
    import pymysql
    filters = filters or kwargs or {}
    conn = _get_mysql_conn()

    try:
        sql = "SELECT id, name, email, phone, status, created_at FROM user WHERE 1=1"
        params: list = []

        if filters.get("status"):
            sql += " AND status = %s"
            params.append(filters["status"])
        if filters.get("keyword"):
            kw = f"%{filters['keyword']}%"
            sql += " AND (name LIKE %s OR email LIKE %s OR phone LIKE %s)"
            params.extend([kw, kw, kw])
        if filters.get("has_email"):
            sql += " AND email IS NOT NULL AND email != ''"
        if filters.get("has_phone"):
            sql += " AND phone IS NOT NULL AND phone != ''"

        sql += " ORDER BY id ASC"
        limit = int(filters.get("limit", 200))
        if limit > 0:
            sql += f" LIMIT {min(limit, 10000)}"

        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [d[0] for d in cur.description]
            rows = [list(row) for row in cur.fetchall()]

        return {
            "ok": True,
            "data": {"columns": columns, "rows": rows, "count": len(rows)},
            "sql": cur._last_executed if hasattr(cur, "_last_executed") else sql,
        }
    except Exception as e:
        log.exception("get_users 查询失败")
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def test_mysql() -> dict:
    try:
        conn = _get_mysql_conn()
        conn.ping()
        conn.close()
        return {"ok": True, "data": {"message": "MySQL 连接正常"}}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════
#  SMTP
# ════════════════════════════════════════════════════

def send_email(to: str, subject: str, body: str, cc: str = "",
               is_html: bool = False, async_mode: bool = False, **kwargs) -> dict:
    if async_mode:
        return enqueue_job("send_email", {
            "to": to, "subject": subject, "body": body,
            "cc": cc, "is_html": is_html,
        })

    smtp = {
        "host": _env_or("smtp.host", ""),
        "port": int(_env_or("smtp.port", "465")),
        "user": _env_or("smtp.user", ""),
        "password": _env_or("smtp.password", ""),
        "ssl": str(_env_or("smtp.ssl", "true")).lower() in ("true", "1", "yes"),
    }

    if not all([smtp["host"], smtp["user"], smtp["password"]]):
        return {"ok": False, "error": "SMTP 未配置"}
    if not to.strip():
        return {"ok": False, "error": "收件人不能为空"}

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp["user"]
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    content_type = "html" if (is_html or body.strip().startswith("<")) else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    if cc:
        recipients += [a.strip() for a in cc.split(",") if a.strip()]

    try:
        if smtp["ssl"]:
            server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=15)
        else:
            server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=15)
            server.starttls()
        server.login(smtp["user"], smtp["password"])
        server.sendmail(smtp["user"], recipients, msg.as_string())
        server.quit()
        _stats["emails_sent"] += 1
        log.info(f"邮件已发送: {recipients}")
        return {"ok": True, "data": {"recipients": recipients}}
    except smtplib.SMTPAuthenticationError:
        _stats["emails_failed"] += 1
        return {"ok": False, "error": "认证失败，请检查授权码"}
    except Exception as e:
        _stats["emails_failed"] += 1
        log.error(f"邮件发送失败: {e}")
        return {"ok": False, "error": str(e)}


def test_smtp() -> dict:
    try:
        result = send_email(
            to=_env_or("smtp.user", ""),
            subject="[MCP Test] 连接测试",
            body="这是 user-messaging MCP Server v2 的测试邮件。",
        )
        if result.get("ok"):
            return {"ok": True, "data": {"message": "SMTP 连接正常"}}
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def batch_send(recipients: list, subject: str, body: str,
               is_html: bool = True, rate: int = 30,
               async_mode: bool = False, **kwargs) -> dict:
    """批量发送邮件，带速率控制和占位符个性化。"""
    if async_mode:
        return enqueue_job("batch_send", {
            "recipients": recipients, "subject": subject,
            "body": body, "is_html": is_html, "rate": rate,
        })

    smtp = {
        "host": _env_or("smtp.host", ""),
        "port": int(_env_or("smtp.port", "465")),
        "user": _env_or("smtp.user", ""),
        "password": _env_or("smtp.password", ""),
        "ssl": str(_env_or("smtp.ssl", "true")).lower() in ("true", "1", "yes"),
    }

    if not all([smtp["host"], smtp["user"], smtp["password"]]):
        return {"ok": False, "error": "SMTP 未配置"}

    delay = 60.0 / rate if rate > 0 else 0
    results = []
    total = len(recipients)

    for i, r in enumerate(recipients):
        email = r.get("email", "").strip()
        name = r.get("name", "").strip()

        if not email:
            results.append({**r, "status": "skip", "error": "邮箱为空"})
            continue

        subj = subject.replace("{name}", name).replace("{email}", email)
        content = body.replace("{name}", name).replace("{email}", email)

        result = send_email(email, subj, content, is_html=is_html)
        status = "ok" if result.get("ok") else "fail"
        results.append({
            "email": email, "name": name, "status": status,
            "error": result.get("error", ""),
        })

        if delay > 0 and i < total - 1:
            time.sleep(delay)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "ok": True,
        "data": {
            "total": total, "ok": ok_count,
            "fail": total - ok_count, "results": results,
        },
    }


# ════════════════════════════════════════════════════
#  SMS (阿里云)
# ════════════════════════════════════════════════════

def send_sms(phone: str, template_code: str = "",
             template_params: dict | None = None,
             async_mode: bool = False, **kwargs) -> dict:
    if async_mode:
        return enqueue_job("send_sms", {
            "phone": phone, "template_code": template_code,
            "template_params": template_params or {},
        })

    access_key = _env_or("sms.access_key", "")
    secret_key = _env_or("sms.secret_key", "")
    sign_name = _env_or("sms.sign_name", "")
    template_code = template_code or _env_or("sms.template_code", "")

    if not access_key or not secret_key:
        return {"ok": False, "error": "短信未配置 access_key/secret_key"}
    if not template_code:
        return {"ok": False, "error": "未指定模板代码"}
    if not phone.strip():
        return {"ok": False, "error": "手机号不能为空"}

    try:
        try:
            from alibabacloud_dysmsapi20170525.client import Client as SmsClient
            from alibabacloud_dysmsapi20170525 import models as sms_models
            from alibabacloud_tea_openapi import models as open_api_models

            cfg = open_api_models.Config(
                access_key_id=access_key, access_key_secret=secret_key,
                endpoint="dysmsapi.aliyuncs.com",
            )
            client = SmsClient(cfg)
            req = sms_models.SendSmsRequest(
                phone_numbers=phone, sign_name=sign_name,
                template_code=template_code,
                template_param=json.dumps(template_params or {}),
            )
            resp = client.send_sms(req)
            if resp.body.code == "OK":
                _stats["sms_sent"] += 1
                log.info(f"短信已发送: {phone}")
                return {"ok": True, "data": {"phone": phone, "biz_id": resp.body.biz_id}}
            else:
                _stats["sms_failed"] += 1
                return {"ok": False, "error": f"{resp.body.code}: {resp.body.message}"}
        except ImportError:
            log.warning(f"短信 SDK 未安装，回退日志模式 phone={phone}")
            _stats["sms_sent"] += 1
            return {"ok": True, "data": {"phone": phone, "fallback": True}}
    except Exception as e:
        _stats["sms_failed"] += 1
        log.error(f"短信发送失败: {e}")
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════
#  健康检查 & 统计
# ════════════════════════════════════════════════════

def health() -> dict:
    redis_ok = False
    r = _get_redis()
    if r:
        try:
            r.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "ok": True,
        "data": {
            "server": SERVER_NAME,
            "version": VERSION,
            "status": "running",
            "uptime_seconds": round(_uptime(), 1),
            "stats": _stats,
            "redis": "connected" if redis_ok else "unavailable",
            "auth_enabled": bool(AUTH_TOKEN),
        },
    }


# ════════════════════════════════════════════════════
#  工具注册表 (函数必须在 TOOL_MAP 之前定义)
# ════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "get_users",
        "description": "从 MySQL user 表查询用户列表，支持 status/has_email/has_phone/keyword/limit 筛选。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status":    {"type": "string",  "description": "状态筛选: active/inactive"},
                "keyword":   {"type": "string",  "description": "模糊匹配 name/email/phone"},
                "has_email": {"type": "boolean", "description": "只返回有邮箱的用户"},
                "has_phone": {"type": "boolean", "description": "只返回有手机号的用户"},
                "limit":     {"type": "integer", "description": "最大返回行数 (默认 200)"},
            },
        },
    },
    {
        "name": "send_email",
        "description": "发送邮件，支持纯文本/HTML。async_mode=true 推入 Redis 队列异步发送。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to":         {"type": "string",  "description": "收件人，多个用逗号分隔"},
                "subject":    {"type": "string",  "description": "主题"},
                "body":       {"type": "string",  "description": "正文 (text 或 HTML)"},
                "cc":         {"type": "string",  "description": "抄送 (可选)"},
                "is_html":    {"type": "boolean", "description": "是否为 HTML (默认 false)"},
                "async_mode": {"type": "boolean", "description": "推入 Redis 队列异步发送"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "batch_send",
        "description": "批量发送邮件。传入 recipients 列表，逐个发送（带速率控制）。支持 {name}/{email} 占位符。async_mode=true 推入 Redis 队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "array", "items": {"type": "object"},
                    "description": '[{"email":"a@x.com","name":"张三"},...]',
                },
                "subject":    {"type": "string",  "description": "主题，支持 {name}/{email}"},
                "body":       {"type": "string",  "description": "正文，支持 {name}/{email}"},
                "is_html":    {"type": "boolean", "description": "是否为 HTML"},
                "rate":       {"type": "integer", "description": "每分钟发送速率 (默认 30)"},
                "async_mode": {"type": "boolean", "description": "推入 Redis 队列异步发送"},
            },
            "required": ["recipients", "subject", "body"],
        },
    },
    {
        "name": "send_sms",
        "description": "发送短信 (阿里云)。async_mode=true 推入 Redis 队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone":           {"type": "string", "description": "手机号"},
                "template_code":   {"type": "string", "description": "模板代码 SMS_xxxxx"},
                "template_params": {"type": "object", "description": '模板参数 {"code":"1234"}'},
                "async_mode":      {"type": "boolean", "description": "推入 Redis 队列"},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "get_job",
        "description": "查询 Redis 异步任务状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "process_queue",
        "description": "处理 Redis 队列中的待发任务（手动触发，通常由定时任务调用）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "一次处理最多几条 (默认 10)"},
            },
        },
    },
    {
        "name": "health",
        "description": "健康检查 — 返回服务器状态、运行统计、Redis 连接状态。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "test_mysql",
        "description": "测试 MySQL 数据库连接。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "test_smtp",
        "description": "测试 SMTP 邮件发送配置（会发测试邮件到发件人自身）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOL_MAP = {
    "get_users":     get_users,
    "send_email":    send_email,
    "batch_send":    batch_send,
    "send_sms":      send_sms,
    "get_job":       get_job_status,
    "process_queue": process_queue,
    "health":        health,
    "test_mysql":    test_mysql,
    "test_smtp":     test_smtp,
}


# ════════════════════════════════════════════════════
#  JSON-RPC 协议处理
# ════════════════════════════════════════════════════

def _rpc_ok(rid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _write(resp: dict):
    line = json.dumps(resp, ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def handle_request(msg: dict) -> None:
    rid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    extra = {"rid": str(rid) if rid else "-"}
    _stats["requests_total"] += 1

    # ── initialize ──
    if method == "initialize":
        log.info("initialize", extra=extra)
        _write(_rpc_ok(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        }))
        _stats["requests_ok"] += 1
        return

    # ── notifications/initialized ──
    if method == "notifications/initialized":
        log.info("initialized", extra=extra)
        return

    # ── tools/list ──
    if method == "tools/list":
        _write(_rpc_ok(rid, {"tools": TOOLS}))
        _stats["requests_ok"] += 1
        return

    # ── tools/call ──
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        auth_err = _check_auth(tool_args)
        if auth_err:
            log.warning(f"tools/call {tool_name} 认证失败", extra=extra)
            _stats["requests_err"] += 1
            _write(_rpc_err(rid, -32001, auth_err))
            return

        func = TOOL_MAP.get(tool_name)
        if not func:
            _stats["requests_err"] += 1
            _write(_rpc_err(rid, -32601, f"未知工具: {tool_name}"))
            return

        log.info(f"tools/call {tool_name}", extra=extra)
        try:
            result = func(**tool_args) if tool_args else func()
            if not isinstance(result, dict) or "ok" not in result:
                result = {"ok": True, "data": result}
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            _write(_rpc_ok(rid, {"content": [{"type": "text", "text": text}]}))
            _stats["requests_ok"] += 1
        except Exception as e:
            log.exception(f"tools/call {tool_name} 异常", extra=extra)
            _stats["requests_err"] += 1
            _write(_rpc_ok(rid, {
                "content": [{"type": "text",
                             "text": json.dumps({"ok": False, "error": str(e)},
                                                ensure_ascii=False)}],
            }))
        return

    # ── ping ──
    if method == "ping":
        _write(_rpc_ok(rid, {}))
        return

    _stats["requests_err"] += 1
    _write(_rpc_err(rid, -32601, f"未知方法: {method}"))


def main():
    load_config()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    log.info(f"{SERVER_NAME} v{VERSION} 启动", extra={"rid": "init"})
    if AUTH_TOKEN:
        log.info("认证已启用", extra={"rid": "init"})
    if _get_redis():
        log.info("Redis 队列已就绪", extra={"rid": "init"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            handle_request(msg)
        except json.JSONDecodeError as e:
            log.error(f"JSON 解析失败: {e}", extra={"rid": "-"})
            _write(_rpc_err(None, -32700, f"Parse error: {e}"))
        except Exception:
            log.exception("未处理异常", extra={"rid": "-"})

    log.info(f"{SERVER_NAME} 退出", extra={"rid": "exit"})


if __name__ == "__main__":
    main()
