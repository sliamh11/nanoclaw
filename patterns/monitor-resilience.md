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
        try:
            raw = subprocess.check_output(
                ["gh", "pr", "view", str(pr), "--json", "headRefOid,statusCheckRollup"],
                text=True, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            print(f"PR #{pr}: gh error (skipping): {e.stderr or e}", flush=True)
            all_green = False
            continue
        data = json.loads(raw)
        checks = data.get("statusCheckRollup") or []
        conclusions = [c.get("conclusion", "") for c in checks]
        if not conclusions:
            status = "PENDING"
        elif all(c == "SUCCESS" for c in conclusions):
            status = "GREEN"
        elif any(c in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for c in conclusions):
            status = "FAILED"
        elif any(c in ("SKIPPED", "NEUTRAL") for c in conclusions):
            status = "PENDING"  # treat skip/neutral as still-pending
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
session writing to a temp file. The backup uses a 3-minute cadence: faster than 3 minutes gains
little (CI state changes slowly) and costs more API calls; slower than 3 minutes grows the
recovery window beyond 15 minutes. GitHub's authenticated API allows 5,000 req/hr; 3 PRs × 20
calls/hr = 60 calls/hr — well under the ceiling. The different cadence (45s vs 180s) ensures
Monitor silence and backup silence cannot synchronize through shared rate-limit windows.

```bash
# Launch backup poll in a separate background session
# Writes current PR states to /tmp/monitor-backup-<pid>.txt every 3 minutes
cat > /tmp/monitor-backup-poll.sh << 'SHELLEOF'
#!/bin/bash
PR_NUMBERS="123 124 125"   # space-separated PR numbers
BACKUP_FILE="/tmp/monitor-backup-$$.txt"  # PID-scoped to avoid cross-contamination
echo "BACKUP_FILE=$BACKUP_FILE"          # print path so parent session can find it

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
    } > "$BACKUP_FILE.tmp" && mv "$BACKUP_FILE.tmp" "$BACKUP_FILE"
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

If the Monitor has emitted **zero events for 10 minutes**, assume it is silent. Read the backup
file — the script prints its path on startup (`BACKUP_FILE=...`); if you missed it, use
`ls -t /tmp/monitor-backup-*.txt | head -1` to find the newest one:

```bash
cat /tmp/monitor-backup-<pid>.txt  # replace <pid> with the backup poll's PID
```

10 minutes bounds the wait to ≤15 minutes (10 min detection + 5 min recovery) while avoiding
false alarms during slow CI (5-8 minutes per job). See RETRO-2026-05-16-07 for the incident
that established this threshold.

**Why backup-poll instead of retry-with-timeout:** A timeout on the Monitor process only
detects total silence. The backup poll surfaces current PR state independently — it works for
partial delivery degradation too (some events arrive, the terminal event is dropped). Retry
would just re-run the same Monitor with the same failure modes.

**Recovery action:**

1. Read the backup file — the backup poll should have current state.
2. If the backup shows all targets are **green/merged**: proceed with the originally planned
   action (merge, release, etc.). Kill the monitor process. Do not wait for Monitor to confirm.
3. If the backup shows any target is **FAILED**: surface to the user immediately with the
   specific PR numbers and failure state. Do not proceed with the merge/release. The backup
   poll's `2>/dev/null || echo "PR#$pr ERROR"` distinguishes network errors from actual CI
   failures — treat `ERROR` as unknown (surface to user), treat `FAILURE` as definitive.
4. If the backup file is stale (older than 5 minutes) or missing: surface to the user with
   the last known state and the monitor's silence duration. Do not merge silently.
5. Log the discrepancy: `echo "Monitor silent for >10m at $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> /tmp/monitor-silence.log`

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
