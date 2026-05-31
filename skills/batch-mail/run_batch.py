"""Batch email sender — communicates with user-messaging MCP server via JSON-RPC over stdio."""
import json
import os
import subprocess
import sys
import time

SERVER_SCRIPT = r"D:\www\pythonPrj\myAI\.claude\mcp-servers\user-messaging\server.py"
SETTINGS_FILE = r"D:\www\pythonPrj\myAI\.claude\settings.local.json"

HTML_BODY = """<h2>系统通知</h2>
<p>您好 {name}：</p>
<p>感谢您一直以来对我们平台的支持与信任。</p>
<p>为了更好地为您服务，我们将持续优化系统性能，提升服务质量。</p>
<p>如有任何疑问，请随时联系客服。</p>
<p>祝您生活愉快！</p>
<br>
<p>—— 系统管理团队</p>
<p style="color:#999;font-size:12px;">此邮件由系统自动发送，请勿回复。</p>"""


def sanitize_str(s):
    """Remove surrogate characters that break UTF-8 JSON encoding."""
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def send_rpc(proc, msg: dict) -> dict:
    """Send a JSON-RPC request and return the response."""
    line = json.dumps(msg, ensure_ascii=False)
    print(f"[REQ] {line[:200]}...")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()

    while True:
        resp_line = proc.stdout.readline()
        if not resp_line:
            continue
        resp = json.loads(resp_line)
        result = resp.get("result", {})
        if isinstance(result, dict):
            keys = list(result.keys())
        else:
            keys = type(result).__name__
        print(f"[RESP] id={resp.get('id')} result={keys}")
        return resp


def load_env_from_settings():
    """Load user-messaging env vars from settings.local.json."""
    env = os.environ.copy()
    try:
        settings = json.loads(open(SETTINGS_FILE, encoding="utf-8").read())
        mcp_env = settings.get("mcpServers", {}).get("user-messaging", {}).get("env", {})
        for k, v in mcp_env.items():
            env[k] = v
        print(f"Loaded {len(mcp_env)} env vars from settings.local.json")
    except Exception as e:
        print(f"Warning: Could not load settings: {e}")
    return env


def main():
    proc_env = load_env_from_settings()

    print("=" * 60)
    print("Starting user-messaging MCP server...")
    proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=proc_env,
    )

    try:
        # Step 1: Initialize
        print("\n>>> Step 1: initialize")
        resp = send_rpc(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "batch-mail-skill", "version": "1.0.0"},
            },
        })
        srv = resp.get("result", {}).get("serverInfo", {})
        print(f"Server: {json.dumps(srv, ensure_ascii=False)}")

        # Step 2: Send initialized notification
        print("\n>>> Step 2: notifications/initialized")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        time.sleep(0.3)

        # Step 3: Get users
        print("\n>>> Step 3: get_users (has_email=true, limit=200)")
        resp = send_rpc(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "get_users",
                "arguments": {"has_email": True, "limit": 200},
            },
        })

        content = resp["result"]["content"][0]["text"]
        users_result = json.loads(content)
        print(f"get_users ok={users_result.get('ok')}, count={users_result.get('data', {}).get('count', 0)}")

        if not users_result.get("ok"):
            print(f"ERROR: get_users failed - {users_result.get('error')}")
            return

        data = users_result["data"]
        columns = data["columns"]
        rows = data["rows"]
        print(f"Columns: {columns}")
        print(f"Total users found: {len(rows)}")

        try:
            name_idx = columns.index("name")
            email_idx = columns.index("email")
        except ValueError as e:
            print(f"ERROR: missing column - {e}")
            return

        recipients = []
        for row in rows:
            email = row[email_idx]
            name = row[name_idx] or ""
            if email and email.strip():
                recipients.append({
                    "name": sanitize_str(name),
                    "email": sanitize_str(email.strip()),
                })

        print(f"Recipients with valid email: {len(recipients)}")
        if recipients:
            print(f"First 3: {json.dumps(recipients[:3], ensure_ascii=False)}")
            print(f"Last 3: {json.dumps(recipients[-3:], ensure_ascii=False)}")

        if not recipients:
            print("ERROR: No recipients with valid email!")
            return

        # Step 4: Batch send
        print(f"\n>>> Step 4: batch_send ({len(recipients)} recipients, async_mode=false, rate=30)")
        print("This may take a while...")

        t_start = time.time()
        resp = send_rpc(proc, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "batch_send",
                "arguments": {
                    "recipients": recipients,
                    "subject": "系统通知",
                    "body": HTML_BODY,
                    "is_html": True,
                    "rate": 30,
                    "async_mode": False,
                },
            },
        })
        t_end = time.time()

        content = resp["result"]["content"][0]["text"]
        batch_result = json.loads(content)

        print("\n" + "=" * 60)
        print("BATCH SEND RESULTS")
        print("=" * 60)
        print(json.dumps(batch_result, ensure_ascii=False, indent=2))
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

    finally:
        print("\nShutting down server...")
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        try:
            stderr_output = proc.stderr.read()
            if stderr_output:
                print("\n[Server stderr]:")
                print(stderr_output[:2000])
        except Exception:
            pass  # Ignore stderr read errors


if __name__ == "__main__":
    main()
