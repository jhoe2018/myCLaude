---
name: batch-mail
description: >
  批量发送邮件/短信——通过 MCP user-messaging 服务器查询用户并发送。
  支持异步队列模式（Redis）、占位符个性化、速率控制、失败重试。
  触发词: "我要发邮件" / "我想发邮件" / "批量发邮件" / "群发邮件" /
  "给用户发邮件" / "邮件群发" / "send batch email" / "bulk mail"
argument-hint: "<邮件主题和内容>"
allowed-tools: "Bash(python *)"
---

# 批量邮件/短信发送

调用 MCP `user-messaging` (v2) 服务器，支持同步和异步队列两种模式。

代码依赖: `../_shared/mail_sender.py` (公共 SMTP + HTML 表格)

## MCP 工具速查

| 工具 | 用途 | 异步? |
|------|------|-------|
| `get_users` | 查 user 表 | - |
| `send_email` | 发单封邮件 | `async_mode: true` |
| `batch_send` | 批量发邮件 | `async_mode: true` |
| `send_sms` | 发短信 | `async_mode: true` |
| `get_job` | 查异步任务状态 | - |
| `process_queue` | 手动处理队列 | - |
| `health` | 健康检查+统计 | - |
| `test_mysql` / `test_smtp` | 连接测试 | - |

## 操作流程

### Step 1: 确认需求
- 邮件主题、内容（支持 `{name}` `{email}` 占位符）
- 筛选条件（全部 / active / 关键字 / 有邮箱的）

### Step 2: 查用户
```
get_users:
  status: "active"
  has_email: true
  limit: 200
```

### Step 3: 选模式

**同步模式 (少量邮件 < 50 封):**
```
batch_send:
  recipients: [...]
  subject: "活动通知"
  body: "<html>...</html>"
  is_html: true
  rate: 30
```

**异步模式 (大量邮件 / 短信):**
```
batch_send:
  recipients: [...]
  subject: "活动通知"
  body: "<html>...</html>"
  async_mode: true      ← 推入 Redis 队列立即返回 job_id
```

### Step 4: 追踪异步任务
```
get_job:
  job_id: "a1b2c3d4"
```

### Step 5: 健康检查 (可选)
```
health  →  uptime / emails_sent / queue_enqueued ...
```

## 响应格式 (统一)

所有 MCP 工具返回统一 JSON:
```json
{"ok": true, "data": {...}}          // 成功
{"ok": false, "error": "原因"}        // 失败
```

## 注意事项

- `UM_AUTH_TOKEN` 非空时启用 token 认证
- Redis 队列依赖 `UM_REDIS_*` 配置，不可用时自动回退同步模式
- 日志: `.claude/mcp-servers/user-messaging/logs/server.log` (按天轮转，保留 30 天)
