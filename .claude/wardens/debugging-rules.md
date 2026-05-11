# Debugging Methodology Rules

> Not a warden agent — a reference methodology loaded by the systematic-debugging pattern.
> Adapted from Superpowers' systematic-debugging. Read when encountering bugs.

## The Four Phases

### Phase 1: Investigate (MUST complete before any fix)
1. Read error messages completely — line numbers, stack traces, exit codes
2. Reproduce consistently — exact steps, every time?
3. Check recent changes — git diff, new deps, config changes
4. Multi-component: log data at EACH component boundary before guessing which layer fails
5. Trace data flow backward — where does the bad value originate?

### Phase 2: Analyze
1. Find working examples of similar code in the same codebase
2. Compare working vs broken — list every difference
3. Understand dependencies and assumptions

### Phase 3: Hypothesize and Test
1. Form ONE specific hypothesis: "X causes Y because Z"
2. Make the SMALLEST change to test it — one variable at a time
3. If it fails, form a NEW hypothesis — don't stack fixes

### Phase 4: Fix
1. Create a failing test BEFORE fixing
2. Implement ONE fix for the root cause
3. Verify: test passes, no regressions

## The 3-Fix Rule
If you've tried 3 fixes and none worked → STOP. This is likely an architecture problem, not a bug. Question the design before attempting fix #4.

## Red Flags
- "Quick fix for now" → root cause unknown
- "Just try X" → skipping Phase 1
- "Add multiple changes" → can't isolate what worked
- "I see the problem" → seeing symptoms ≠ understanding cause
- Proposing solutions before tracing data flow
