---
name: send-email
description: >
  从 MySQL 数据库查询数据并发送邮件。数据来源为 MCP MySQL 服务器，
  查询结果格式化为 HTML 表格后通过 SMTP 发送。
  触发词: "发邮件"、"发送报表"、"查询并发送"、"email report"、
  "send email"、"邮件报表"、"数据邮件"。
argument-hint: "<SQL/自然语言查询> --to 收件人 [--subject 主题]"
allowed-tools: "Bash(python *)"
---

# MySQL 数据邮件发送

从 MCP MySQL 查询数据，生成 HTML 表格，通过 SMTP 发送邮件。

## 文件清单

| 文件 | 用途 |
|------|------|
| `send_mail.py` | SMTP 邮件发送 |
| `table_builder.py` | 查询结果 → HTML 表格 |
| `email_template.html` | 邮件 HTML 模板 |
| `query_and_send.py` | 一键查询+发送（从 stdin 读 JSON） |
| `mail_config.json` | SMTP 配置 |

## 前置配置

### 1. MySQL MCP
确保 `.claude/settings.local.json` 中 `mcpServers.mysql` 已正确配置。

### 2. SMTP 发件配置（三选一）

方式A 命令行:
```
--mail-type qq --user xx@qq.com --password 授权码
```

方式B 环境变量:
```bash
export SMTP_HOST=smtp.qq.com SMTP_PORT=465 SMTP_USER=xx@qq.com SMTP_PASS=授权码
```

方式C 配置文件: 编辑 `mail_config.json`
```json
{"host": "smtp.qq.com", "port": 465, "user": "xx@qq.com", "password": "授权码", "ssl": true}
```

| `--mail-type` | SMTP | 端口 |
|---------------|------|------|
| qq | smtp.qq.com | 465 |
| 163 | smtp.163.com | 465 |
| gmail | smtp.gmail.com | 587 |
| exmail | smtp.exmail.qq.com | 465 |

> QQ/163 需使用**授权码**而非登录密码。

## 操作流程

### Step 1: 确认需求
- 查询什么数据（SQL 或自然语言）
- 收件人、主题

### Step 2: 通过 MCP MySQL 执行查询
先了解表结构（`SHOW TABLES` / `DESCRIBE <table>`），再执行查询。

### Step 3: 生成 HTML 并发送

**方式A — 一键管道:**
```bash
echo '<MCP查询结果的JSON>' | python ${CLAUDE_SKILL_DIR}/query_and_send.py \
  --to "user@example.com" --subject "数据报表"
```

**方式B — 分步:**
```bash
# 1. 用 table_builder.py 生成 HTML
python ${CLAUDE_SKILL_DIR}/table_builder.py '<JSON数据>' > /tmp/report.html

# 2. 用 send_mail.py 发送
python ${CLAUDE_SKILL_DIR}/send_mail.py \
  --to "user@example.com" --subject "数据报表" \
  --body-file /tmp/report.html --html
```

首次使用追加 SMTP 参数: `--mail-type qq --user xx@qq.com --password 授权码`

## 注意事项

- MySQL MCP 为只读，仅支持 SELECT
- 收件人多个用英文逗号: `a@x.com,b@x.com`
- 查询结果超 1000 行建议分批或聚合
