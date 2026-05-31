"""user-messaging MCP Mock Server — 纯内存模拟，不依赖 MySQL/Redis/SMTP。

用途: 开发测试 batch-mail skill，无需真实后端。
注册到 settings.local.json 替换 server.py 即可。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "2.0.0-mock"
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "user-messaging-mock"

_start_time = time.time()
_stats = dict.fromkeys([
    "requests_total", "requests_ok", "requests_err",
    "emails_sent", "emails_failed", "sms_sent", "sms_failed",
    "queue_enqueued", "queue_processed", "queue_failed",
], 0)

# ── 内存数据 ───────────────────────────────────────

_MOCK_USERS = [
    {"id": 1,  "name": "Zhang Wei",      "email": "zhangwei@qq.com",       "phone": "13800138001", "status": "active",   "created_at": "2026-01-15 10:00:00"},
    {"id": 2,  "name": "Li Fang",        "email": "lifang@163.com",        "phone": "13800138002", "status": "active",   "created_at": "2026-01-20 14:30:00"},
    {"id": 3,  "name": "Wang Lei",       "email": "wanglei@gmail.com",     "phone": "13800138003", "status": "active",   "created_at": "2026-02-01 09:00:00"},
    {"id": 4,  "name": "Chen Jing",      "email": "chenjing@qq.com",       "phone": "13800138004", "status": "active",   "created_at": "2026-02-10 16:00:00"},
    {"id": 5,  "name": "Liu Qiang",      "email": "liuqiang@126.com",      "phone": "13800138005", "status": "inactive", "created_at": "2026-02-15 11:00:00"},
    {"id": 6,  "name": "Yang Min",       "email": "yangmin@sina.com",      "phone": "13800138006", "status": "active",   "created_at": "2026-03-01 08:00:00"},
    {"id": 7,  "name": "Zhao Tao",       "email": "zhaotao@outlook.com",   "phone": "",            "status": "active",   "created_at": "2026-03-10 13:00:00"},
    {"id": 8,  "name": "Huang Yan",      "email": "huangyan@qq.com",       "phone": "13800138008", "status": "active",   "created_at": "2026-03-20 15:00:00"},
    {"id": 9,  "name": "Zhou Ping",      "email": "zhouping@163.com",      "phone": "13800138009", "status": "banned",   "created_at": "2026-04-01 10:00:00"},
    {"id": 10, "name": "Wu Jie",         "email": "wujie@gmail.com",       "phone": "13800138010", "status": "active",   "created_at": "2026-04-10 12:00:00"},
    {"id": 11, "name": "No Email User",  "email": "",                      "phone": "13800138999", "status": "active",   "created_at": "2026-04-15 09:00:00"},
    {"id": 12, "name": "No Phone User",  "email": "nophone@test.com",      "phone": "",            "status": "active",   "created_at": "2026-04-20 14:00:00"},
    {"id": 13, "name": "No Contact",     "email": "",                      "phone": "",            "status": "inactive", "created_at": "2026-05-01 08:00:00"},
    {"id": 14, "name": "Sun Hua",        "email": "sunhua@aliyun.com",     "phone": "13800138014", "status": "active",   "created_at": "2026-05-10 17:00:00"},
    {"id": 15, "name": "Ma Xin",         "email": "maxin@qq.com",          "phone": "13800138015", "status": "active",   "created_at": "2026-05-20 11:00:00"},
    {"id": 16, "name": "Hu Bo",          "email": "hubo@163.com",          "phone": "13800138016", "status": "active",   "created_at": "2026-05-25 10:00:00"},
    {"id": 17, "name": "Zhu Rui",        "email": "zhurui@gmail.com",      "phone": "13800138017", "status": "active",   "created_at": "2026-05-28 16:00:00"},
    {"id": 18, "name": "Guo Ning",       "email": "guoning@126.com",       "phone": "13800138018", "status": "active",   "created_at": "2026-05-30 09:00:00"},
    {"id": 19, "name": "He Chao",        "email": "hechao@sina.com",       "phone": "13800138019", "status": "inactive", "created_at": "2026-05-30 14:00:00"},
    {"id": 20, "name": "Luo Feng",       "email": "luofeng@qq.com",        "phone": "13800138020", "status": "active",   "created_at": "2026-05-31 10:00:00"},
]

_queues: dict[str, list[dict]] = {}
_jobs: dict[str, dict] = {}


# ── 工具函数 ───────────────────────────────────────

def get_users(filters: dict | None = None, **kwargs) -> dict:
    filters = filters or kwargs or {}
    result = list(_MOCK_USERS)

    if filters.get("status"):
        result = [u for u in result if u["status"] == filters["status"]]
    if filters.get("has_email"):
        result = [u for u in result if u["email"]]
    if filters.get("has_phone"):
        result = [u for u in result if u["phone"]]
    if filters.get("keyword"):
        kw = filters["keyword"].lower()
        result = [u for u in result if kw in u["name"].lower()
                  or kw in u["email"].lower() or kw in u["phone"]]

    limit = int(filters.get("limit", 200))
    result = result[:min(limit, 10000)]

    return {
        "ok": True,
        "data": {
            "columns": ["id", "name", "email", "phone", "status", "created_at"],
            "rows": [[u[k] for k in ("id", "name", "email", "phone", "status", "created_at")]
                     for u in result],
            "count": len(result),
        },
    }


def test_mysql() -> dict:
    return {"ok": True, "data": {"message": "Mock MySQL — always OK"}}


def send_email(to: str, subject: str, body: str, cc: str = "",
               is_html: bool = False, async_mode: bool = False, **kwargs) -> dict:
    if async_mode:
        return enqueue_job("send_email", {
            "to": to, "subject": subject, "body": body, "cc": cc, "is_html": is_html,
        })

    _stats["emails_sent"] += 1
    return {
        "ok": True,
        "data": {"recipients": [a.strip() for a in to.split(",") if a.strip()],
                 "mock": True},
    }


def test_smtp() -> dict:
    return {"ok": True, "data": {"message": "Mock SMTP — always OK"}}


def batch_send(recipients: list, subject: str, body: str,
               is_html: bool = True, rate: int = 30,
               async_mode: bool = False, **kwargs) -> dict:
    if async_mode:
        return enqueue_job("batch_send", {
            "recipients": recipients, "subject": subject,
            "body": body, "is_html": is_html, "rate": rate,
        })

    results = []
    for r in recipients:
        email = r.get("email", "").strip()
        name = r.get("name", "").strip()
        if not email:
            results.append({**r, "status": "skip", "error": "邮箱为空"})
        else:
            _stats["emails_sent"] += 1
            results.append({"email": email, "name": name, "status": "ok",
                            "mock": True})

    ok = sum(1 for r in results if r["status"] == "ok")
    return {"ok": True, "data": {"total": len(recipients), "ok": ok,
                                  "fail": len(recipients) - ok, "results": results,
                                  "mock": True}}


def send_sms(phone: str, template_code: str = "",
             template_params: dict | None = None,
             async_mode: bool = False, **kwargs) -> dict:
    if async_mode:
        return enqueue_job("send_sms", {
            "phone": phone, "template_code": template_code,
            "template_params": template_params or {},
        })

    _stats["sms_sent"] += 1
    return {"ok": True, "data": {"phone": phone, "mock": True}}


def enqueue_job(job_type: str, payload: dict) -> dict:
    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id": job_id, "type": job_type, "payload": payload,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _queues.setdefault(job_type, []).append(job)
    _jobs[job_id] = job
    _stats["queue_enqueued"] += 1
    return {"ok": True, "data": {"job_id": job_id}, "message": f"任务 {job_id} 已入队 (mock)"}


def get_job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        return {"ok": False, "error": f"任务 {job_id} 不存在"}
    return {"ok": True, "data": dict(job)}


def process_queue(limit: int = 10) -> dict:
    processed = 0
    for job_type in ("send_email", "send_sms"):
        for _ in range(limit):
            if not _queues.get(job_type):
                break
            job = _queues[job_type].pop(0)
            job["status"] = "done"
            job["processed_at"] = datetime.now(timezone.utc).isoformat()
            job["result"] = {"ok": True, "mock": True}
            _jobs[job["job_id"]] = job
            processed += 1
            _stats["queue_processed"] += 1

    return {"ok": True, "data": {"processed": processed, "mock": True}}


def health() -> dict:
    return {
        "ok": True,
        "data": {
            "server": SERVER_NAME,
            "version": VERSION,
            "status": "running",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "stats": _stats,
            "redis": "mock",
            "auth_enabled": False,
            "mock": True,
        },
    }


# ── 工具注册表 ─────────────────────────────────────

TOOLS = [
    {
        "name": "get_users",
        "description": "[MOCK] 从内存查询用户列表，支持 status/has_email/has_phone/keyword/limit。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status":    {"type": "string",  "description": "active/inactive/banned"},
                "keyword":   {"type": "string",  "description": "模糊匹配 name/email/phone"},
                "has_email": {"type": "boolean", "description": "只返回有邮箱的"},
                "has_phone": {"type": "boolean", "description": "只返回有手机号的"},
                "limit":     {"type": "integer", "description": "最大返回行数 (默认 200)"},
            },
        },
    },
    {
        "name": "send_email",
        "description": "[MOCK] 模拟发送邮件，async_mode=true 推入内存队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to":         {"type": "string",  "description": "收件人"},
                "subject":    {"type": "string",  "description": "主题"},
                "body":       {"type": "string",  "description": "正文"},
                "cc":         {"type": "string",  "description": "抄送"},
                "is_html":    {"type": "boolean", "description": "是否 HTML"},
                "async_mode": {"type": "boolean", "description": "推入队列"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "batch_send",
        "description": "[MOCK] 模拟批量发送，支持 {name}/{email}。async_mode=true 推入队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipients": {"type": "array",  "items": {"type": "object"}},
                "subject":    {"type": "string", "description": "主题"},
                "body":       {"type": "string", "description": "正文"},
                "is_html":    {"type": "boolean"},
                "rate":       {"type": "integer"},
                "async_mode": {"type": "boolean"},
            },
            "required": ["recipients", "subject", "body"],
        },
    },
    {
        "name": "send_sms",
        "description": "[MOCK] 模拟发送短信。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone":           {"type": "string"},
                "template_code":   {"type": "string"},
                "template_params": {"type": "object"},
                "async_mode":      {"type": "boolean"},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "get_job",
        "description": "[MOCK] 查询内存队列任务状态。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "process_queue",
        "description": "[MOCK] 处理内存队列。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
    {
        "name": "health",
        "description": "[MOCK] 健康检查。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "test_mysql",
        "description": "[MOCK] 永远 OK。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "test_smtp",
        "description": "[MOCK] 永远 OK。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOL_MAP = {
    "get_users": get_users,     "send_email": send_email,
    "batch_send": batch_send,   "send_sms": send_sms,
    "get_job": get_job_status,  "process_queue": process_queue,
    "health": health,           "test_mysql": test_mysql,
    "test_smtp": test_smtp,
}


# ── JSON-RPC ────────────────────────────────────────

def _rpc_ok(rid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _write(resp: dict):
    sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def handle(msg: dict):
    rid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})
    _stats["requests_total"] += 1

    if method == "initialize":
        _write(_rpc_ok(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        }))
        _stats["requests_ok"] += 1
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _write(_rpc_ok(rid, {"tools": TOOLS}))
        _stats["requests_ok"] += 1
        return

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        func = TOOL_MAP.get(tool_name)
        if not func:
            _stats["requests_err"] += 1
            _write(_rpc_err(rid, -32601, f"未知工具: {tool_name}"))
            return
        try:
            result = func(**tool_args) if tool_args else func()
            if not isinstance(result, dict) or "ok" not in result:
                result = {"ok": True, "data": result}
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            _write(_rpc_ok(rid, {"content": [{"type": "text", "text": text}]}))
            _stats["requests_ok"] += 1
        except Exception as e:
            _stats["requests_err"] += 1
            _write(_rpc_ok(rid, {
                "content": [{"type": "text",
                             "text": json.dumps({"ok": False, "error": str(e)},
                                                ensure_ascii=False)}],
            }))
        return

    if method == "ping":
        _write(_rpc_ok(rid, {}))
        return

    _stats["requests_err"] += 1
    _write(_rpc_err(rid, -32601, f"未知方法: {method}"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            _write(_rpc_err(None, -32700, "Parse error"))
        except Exception:
            pass


if __name__ == "__main__":
    main()
