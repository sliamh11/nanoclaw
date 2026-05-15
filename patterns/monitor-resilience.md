---
governs: []
# governs: [] — this pattern covers agent orchestration behavior, not specific source paths.
# No automated drift-check fires on empty governs. Manual re-review obligation: revisit
# whenever Monitor tool behavior or gh CLI PR/check commands change.
last_verified: "2026-05-16"
test_tasks:
  - "Dispatch 5 background agents and watch all PRs reach green CI state before merging"
  - "Monitor a fleet merge and detect if the Monitor tool goes silent for more than 10 minutes"
  - "Watch a background session completion and fall back to backup poll if Monitor emits no events"
  - "Gate a release merge on all CI checks green using Monitor with backup poll discipline"
---
# Pattern: monitor-resilience

## When to apply

Use this pattern whenever you arm a Monitor for a **high-stakes** outcome:

- Fleet merge (≥2 PRs, irreversible once merged)
- All-green CI gate before a merge or release
- Background session completion you cannot afford to miss

Silence from the Monitor tool is **not** success. The tool can fail silently — emitting zero
events due to an upstream routing drop or crash — and this is indistinguishable from "still
waiting." This has caused indefinite waits in production. Pair every high-stakes Monitor with an
independent backup poll.

## Primary: Monitor configuration

Every high-stakes Monitor must have:

1. **A 45-second poll interval** — respects GitHub API rate limits, matches typical CI step duration.
2. **An internal exit condition** — exit when all tracked targets reach the terminal state (green,
   merged, completed). An infinite loop accumulates API calls and masks silent failures.
3. **Change-only emission** — emit one line per state change, not on every poll. Noisy output
   makes silence detection impossible.

```bash
# Example: track CI across multiple PRs, exit when all green
python3 - << 'PYEOF'
import subprocess, json, time, sys

prs = [123, 124, 125]  # replace with actual PR numbers
states = {}

while True:
    all_green = True
    for pr in prs:
        raw = subprocess.check_output(
            ["gh", "pr", "view", str(pr), "--json", "headRefOid,statusCheckRollup"],
            text=True
        )
        data = json.loads(raw)
        checks = data.get("statusCheckRollup") or []
        conclusions = [c.get("conclusion", "") for c in checks]
        if not conclusions:
            status = "PENDING"
        elif all(c == "SUCCESS" for c in conclusions):
            status = "GREEN"
        elif any(c in ("FAILURE", "CANCELLED") for c in conclusions):
            status = "FAILED"
        else:
            status = "PENDING"

        if states.get(pr) != status:
            states[pr] = status
            print(f"PR #{pr}: {status}", flush=True)

        if status != "GREEN":
            all_green = False

    if all_green:
        print("ALL GREEN — exiting monitor", flush=True)
        sys.exit(0)

    time.sleep(45)
PYEOF
```

## Backup poll (required for high-stakes monitors)

For every high-stakes Monitor, launch an **independent backup poll** in a separate background
session writing to a temp file. The backup uses a different cadence (3 minutes) so a Monitor
silence and backup poll silence cannot mask each other through rate-limit synchronization.

```bash
# Launch backup poll in a separate background session
# Writes current PR states to /tmp/monitor-backup-<pid>.txt every 3 minutes
cat > /tmp/monitor-backup-poll.sh << 'SHELLEOF'
#!/bin/bash
PR_NUMBERS="123 124 125"   # space-separated PR numbers
BACKUP_FILE="/tmp/monitor-backup-$$.txt"  # PID-scoped to avoid cross-contamination

while true; do
    TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    {
        echo "=== backup poll at $TIMESTAMP ==="
        for pr in $PR_NUMBERS; do
            STATE=$(gh pr view "$pr" \
                --json number,title,state,statusCheckRollup \
                --jq '"PR#\(.number) \(.state) \(.statusCheckRollup // [] | map(.conclusion) | unique | join(","))"' \
                2>/dev/null || echo "PR#$pr ERROR")
            echo "$STATE"
        done
    } > "$BACKUP_FILE"
    sleep 180
done
SHELLEOF
chmod +x /tmp/monitor-backup-poll.sh
```

Run it as a background Bash session:

```
Tool: Bash
Command: bash /tmp/monitor-backup-poll.sh
run_in_background: true
```

## Silence detection (10-minute threshold)

If the Monitor has emitted **zero events for 10 minutes**, assume it is silent and read the
backup file:

```bash
cat /tmp/monitor-backup-*.txt  # read the PID-scoped backup file
```

10 minutes bounds the wait to ≤15 minutes (10 min detection + 5 min recovery) while avoiding
false alarms during slow CI (5-8 minutes per job). See RETRO-2026-05-16-07 for the incident
that established this threshold.

**Recovery action:**

1. Read `/tmp/monitor-backup-<pid>.txt` — the backup poll should have current state.
2. If the backup shows all targets are terminal (green/merged/failed): act on that state and
   kill the monitor process. Do not wait for the Monitor to confirm.
3. If the backup file is stale or missing: surface to the user with the last known state and
   the monitor's silence duration. Do not merge silently.
4. Log the discrepancy: `echo "Monitor silent for >10m at $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> /tmp/monitor-silence.log`

## Anti-patterns

| Anti-pattern | Why it fails |
|---|---|
| Monitor as the sole signal source | Silent failure = indefinite wait |
| No internal exit condition | Accumulates API calls, runs forever after all targets are terminal |
| Backup poll in the same process as Monitor | A crash takes both; defeats the purpose |
| Backup poll cadence identical to Monitor (45s) | Rate-limit synchronization can silence both simultaneously |
| Acting on Monitor silence as confirmation | Silence is not success |

## Scope

Apply this pattern to any session where you arm a Monitor for a high-stakes gate. This is not
limited to fleet merges — it applies to any Monitor that blocks progress for more than a few
minutes or guards an irreversible action.

This pattern elaborates the `feedback_silent_monitor_antipattern` principle ("silence is not
success") codified in the 2026-05-16 retrospective (RETRO-2026-05-16-07). If the two drift,
the pattern file is the repo-side source of truth; the vault atom is the historical record.
