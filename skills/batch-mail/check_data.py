"""Check user data for problematic characters."""
import json
import os
import subprocess
import sys

SERVER_SCRIPT = r"D:\www\pythonPrj\myAI\.claude\mcp-servers\user-messaging\server.py"
SETTINGS_FILE = r"D:\www\pythonPrj\myAI\.claude\settings.local.json"

# Load env
env = os.environ.copy()
settings = json.loads(open(SETTINGS_FILE, encoding="utf-8").read())
mcp_env = settings.get("mcpServers", {}).get("user-messaging", {}).get("env", {})
for k, v in mcp_env.items():
    env[k] = v

proc = subprocess.Popen(
    [sys.executable, SERVER_SCRIPT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", env=env,
)

# Initialize
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "check", "version": "1.0.0"}}}) + "\n")
proc.stdin.flush()
proc.stdout.readline()

# Initialized
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
proc.stdin.flush()

# Get users
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "get_users", "arguments": {"has_email": True, "limit": 200}}}) + "\n")
proc.stdin.flush()

resp = json.loads(proc.stdout.readline())
content = resp["result"]["content"][0]["text"]
users = json.loads(content)
data = users["data"]
columns = data["columns"]
rows = data["rows"]

name_idx = columns.index("name")
email_idx = columns.index("email")

print(f"Checking {len(rows)} users for problematic characters...")
problematic = []
for i, row in enumerate(rows):
    name = row[name_idx] or ""
    email = row[email_idx] or ""
    try:
        json.dumps({"name": name, "email": email}, ensure_ascii=False)
    except UnicodeEncodeError as e:
        problematic.append((i, name, email, str(e)))
        print(f"PROBLEM #{i}: name={repr(name)} email={repr(email)} error={e}")

if problematic:
    print(f"\n{len(problematic)} problematic records found:")
    for p in problematic:
        print(f"  Row {p[0]}: name={p[1][:50]} email={p[2]}")
else:
    print("No problematic characters found in any record.")

# Also test: encode all recipients as a list
recipients = []
for row in rows:
    email = row[email_idx] or ""
    name = row[name_idx] or ""
    if email.strip():
        recipients.append({"name": name, "email": email.strip()})

print(f"\nTesting JSON encode of all {len(recipients)} recipients...")
try:
    j = json.dumps(recipients, ensure_ascii=False)
    print(f"SUCCESS: {len(j)} bytes")
except Exception as e:
    print(f"FAILED: {e}")

proc.stdin.close()
proc.terminate()
proc.wait()
