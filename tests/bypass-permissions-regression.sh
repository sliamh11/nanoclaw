#!/usr/bin/env bash
# Bypass-permissions regression test (macOS only).
# Dispatches a claude --bg session, verifies Bash tool executes without
# permission gates. Exits 0 on pass, 1 on failure.
# Requires: claude CLI, python3. Cannot run in CI (needs live daemon).

if [[ "$(uname)" != "Darwin" ]]; then
  echo "SKIP: macOS-only test"
  exit 0
fi

if ! command -v claude &>/dev/null; then
  echo "FAIL: claude CLI not found"
  exit 1
fi

TIMEOUT=180
POLL_INTERVAL=5
NAME="bypass-perm-regtest-$$"

echo "Launching bg session: $NAME"
RAW=$(claude --bg --name "$NAME" "Run 'ls /tmp/' via Bash and list any 3 entries you see. Keep your response brief." 2>&1)
SID=$(echo "$RAW" | sed $'s/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -oE '[a-f0-9]{8}' | head -1)

if [[ -z "$SID" ]]; then
  echo "FAIL: could not extract session ID from claude --bg output"
  echo "Raw output: $RAW"
  exit 1
fi

STATE_FILE="$HOME/.claude/jobs/$SID/state.json"
echo "Session ID: $SID"
echo "Polling $STATE_FILE for completion (timeout ${TIMEOUT}s)..."

elapsed=0
while true; do
  if [[ -f "$STATE_FILE" ]]; then
    current_state=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('state',''))" 2>/dev/null)
    if [[ "$current_state" == "done" ]]; then
      break
    fi
  fi
  if (( elapsed >= TIMEOUT )); then
    echo "FAIL: session did not complete within ${TIMEOUT}s"
    claude stop "$SID" 2>/dev/null
    exit 1
  fi
  sleep "$POLL_INTERVAL"
  (( elapsed += POLL_INTERVAL ))
done

echo "Session completed. Checking criteria..."

TRANSCRIPT=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('linkScanPath',''))" 2>/dev/null)
if [[ -z "$TRANSCRIPT" || ! -f "$TRANSCRIPT" ]]; then
  echo "FAIL: transcript not found (linkScanPath=$TRANSCRIPT)"
  exit 1
fi

TRANSCRIPT="$TRANSCRIPT" python3 -c "
import json, os, sys

transcript = os.environ['TRANSCRIPT']
bash_invoked = False
bash_tool_id = None
tool_executed = False
permission_gate = False

with open(transcript) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        att = entry.get('attachment', {})
        if isinstance(att, dict) and att.get('type') == 'hook_blocking_error':
            hook_event = att.get('hookEvent', '')
            if hook_event == 'PreToolUse':
                permission_gate = True

        msg = entry.get('message', {})
        content = msg.get('content', '')
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get('type', '')

            if bt == 'tool_use' and block.get('name') == 'Bash':
                cmd = block.get('input', {}).get('command', '')
                if 'ls /tmp' in cmd or 'ls /tmp/' in cmd:
                    bash_invoked = True
                    bash_tool_id = block.get('id', '')

            # tool_result for the Bash ls /tmp call with non-empty content
            if bt == 'tool_result' and bash_tool_id:
                if block.get('tool_use_id') == bash_tool_id:
                    rc = block.get('content', '')
                    has_content = False
                    if isinstance(rc, str) and len(rc.strip()) > 0:
                        has_content = True
                    elif isinstance(rc, list) and any(
                        isinstance(b, dict) and b.get('text', '').strip()
                        for b in rc
                    ):
                        has_content = True
                    if has_content:
                        tool_executed = True

status = 0

print('Criterion 1: Session completed ........... PASS')

if bash_invoked:
    print('Criterion 2: Bash tool invoked ........... PASS')
else:
    print('Criterion 2: Bash tool invoked ........... FAIL')
    status = 1

if tool_executed:
    print('Criterion 3: Tool executed (not gated) ... PASS')
else:
    print('Criterion 3: Tool executed (not gated) ... FAIL')
    status = 1

if not permission_gate:
    print('Criterion 4: No permission gate .......... PASS')
else:
    print('Criterion 4: No permission gate .......... FAIL')
    status = 1

sys.exit(status)
"
