"""Batch email sender v2 — import server module directly, bypass JSON-RPC."""
import json
import os
import sys
import time

# Set up env vars from settings
SETTINGS_FILE = r"D:\www\pythonPrj\myAI\.claude\settings.local.json"
SERVER_DIR = r"D:\www\pythonPrj\myAI\.claude\mcp-servers\user-messaging"

settings = json.loads(open(SETTINGS_FILE, encoding="utf-8").read())
mcp_env = settings.get("mcpServers", {}).get("user-messaging", {}).get("env", {})
for k, v in mcp_env.items():
    os.environ[k] = v

# Patch json.dumps to handle surrogate characters
_original_dumps = json.dumps

def safe_dumps(obj, **kwargs):
    """json.dumps wrapper: fallback to ensure_ascii=True on UnicodeEncodeError."""
    try:
        return _original_dumps(obj, **kwargs)
    except UnicodeEncodeError:
        kwargs["ensure_ascii"] = True
        return _original_dumps(obj, **kwargs)

json.dumps = safe_dumps

# Add server dir to path and import
sys.path.insert(0, SERVER_DIR)
import server

HTML_BODY = """<h2>系统通知</h2>
<p>您好 {name}：</p>
<p>感谢您一直以来对我们平台的支持与信任。</p>
<p>为了更好地为您服务，我们将持续优化系统性能，提升服务质量。</p>
<p>如有任何疑问，请随时联系客服。</p>
<p>祝您生活愉快！</p>
<br>
<p>—— 系统管理团队</p>
<p style="color:#999;font-size:12px;">此邮件由系统自动发送，请勿回复。</p>"""

print("=" * 60)
print("Step 1: get_users (has_email=true, limit=200)")
users_result = server.get_users(has_email=True, limit=200)
print(f"ok={users_result.get('ok')}, count={users_result.get('data', {}).get('count', 0)}")

if not users_result.get("ok"):
    print(f"ERROR: {users_result.get('error')}")
    sys.exit(1)

data = users_result["data"]
columns = data["columns"]
rows = data["rows"]
print(f"Columns: {columns}")
print(f"Total: {len(rows)}")

name_idx = columns.index("name")
email_idx = columns.index("email")

recipients = []
for row in rows:
    email = row[email_idx]
    name = row[name_idx] or ""
    if email and email.strip():
        recipients.append({"name": name, "email": email.strip()})

print(f"Recipients with valid email: {len(recipients)}")
print(f"First 3: {json.dumps(recipients[:3], ensure_ascii=False)}")
print(f"Last 3: {json.dumps(recipients[-3:], ensure_ascii=False)}")

print(f"\n{'='*60}")
print(f"Step 2: batch_send ({len(recipients)} recipients, async_mode=false, rate=30)")
print("This may take a while...")
t_start = time.time()

batch_result = server.batch_send(
    recipients=recipients,
    subject="系统通知",
    body=HTML_BODY,
    is_html=True,
    rate=30,
    async_mode=False,
)

t_end = time.time()

print(f"\n{'='*60}")
print("BATCH SEND RESULTS")
print("=" * 60)
print(json.dumps(batch_result, ensure_ascii=False, indent=2, default=str))
print(f"\nElapsed: {t_end - t_start:.1f}s")

if batch_result.get("ok"):
    d = batch_result["data"]
    print(f"\nFinal: total={d['total']} ok={d['ok']} fail={d['fail']}")
    failures = [r for r in d.get("results", []) if r.get("status") != "ok"]
    if failures:
        print(f"\nFailed ({len(failures)}):")
        for f in failures[:20]:
            print(f"  {f.get('email')} ({f.get('name')}): {f.get('error')}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
else:
    print(f"\nERROR: {batch_result.get('error')}")
