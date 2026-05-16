# Using Different AI Backends

Deus is backend-neutral: the same assistant experience runs on different LLMs. Claude is the default and most battle-tested backend. OpenAI (GPT-4o via Responses API) is opt-in and approaching parity.

## Quick Start

### Use OpenAI instead of Claude

```bash
# Option A: API key
echo 'OPENAI_API_KEY=sk-...' >> .env

# Option B: Codex OAuth (no API key needed)
codex login

# Then set OpenAI as the default backend
echo 'DEUS_AGENT_BACKEND=openai' >> .env

# Restart Deus
deus auth
```

If both an API key and Codex OAuth are present, the API key takes priority.

### Use OpenAI for one group only

Leave the global default as Claude, override a specific group:

```bash
# Via the CLI
deus openai    # Start a one-off OpenAI session

# Via group config (persisted)
# Set containerConfig.agentBackend = 'openai' on the group
```

### Use OpenAI for one scheduled task only

Set `agent_backend: 'openai'` when creating the task. The rest of the group stays on Claude.

### Switch back to Claude

Remove `DEUS_AGENT_BACKEND` from `.env` (or set it to `claude`) and restart.

## Resolution Order

When Deus decides which backend to use for a message or task:

1. **Task override** — if a scheduled task has `agent_backend` set, use that
2. **Group override** — if the group has `containerConfig.agentBackend` set, use that
3. **Global default** — `DEUS_AGENT_BACKEND` env var (default: `claude`)

## What Stays the Same

Regardless of backend, Deus preserves:

- Same persona, tone, and memory
- Same tool access (shell, filesystem, web, browser, IPC)
- Same chat commands (/compact, /settings, etc.)
- Same session management and idle reset
- Same scheduled task execution
- Same channel support (WhatsApp, Telegram, Slack, Discord, Gmail)
- Same context loading (CLAUDE.md, group config, registered context files)

## What Differs

| Feature | Claude | OpenAI | llama.cpp |
|---------|--------|--------|-----------|
| Tool streaming | Yes (live output) | No (batch response) | No (batch response) |
| Session protocol | Claude Code SDK | OpenAI Responses API | OpenAI chat-completions (in-memory history) |
| Model default | Claude (via SDK) | gpt-4o (configurable via `DEUS_OPENAI_MODEL`) | configured via `LLAMA_CPP_MODEL` (default Gemma-3-1B GGUF from the `/add-llama-cpp` skill) |
| Handoffs | Not yet | Not yet | Not yet |
| MCP tools | Native | Bridged via tool-broker | Bridged via tool-broker (same path as OpenAI) |
| Multimodal | Yes | Yes (gpt-4o) | No (default GGUF is text-only) |
| `/compact` | Native | LLM-summary via Responses | Truncation (system + last N turns); summary-based is a follow-up |
| Credential routing | Through credential proxy | Through credential proxy (`/openai` route) | No proxy — direct call to local `llama-server` (no auth) |

## Known Parity Gaps

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the current parity status and [agent-agnostic-debt.md](agent-agnostic-debt.md) for the tracked gap register.

Key gaps as of this writing:
- OpenAI backend has not been verified end-to-end in production containers
- Dynamic skill parity depends on skills exposing MCP-style tools
- Agents SDK handoffs/tracing not yet implemented for OpenAI
- llama.cpp backend: no `deus llama` foreground CLI shorthand yet (use `deus backend set llama-cpp` and channel messages or scheduled tasks); `/compact` is history truncation only; multimodal default is off; tool-call reliability varies by GGUF model

## CLI Usage

```bash
deus              # Uses default backend (claude unless configured otherwise)
deus claude       # Force Claude for this session
deus codex        # Force Codex (OpenAI) for this session
```

### Codex CLI memory MCP

To give direct Codex CLI sessions the same Deus memory recall tool, register
the repo launcher instead of calling `python3` directly:

```bash
codex mcp add deus-memory -- /path/to/deus/scripts/deus-memory-mcp
```

The launcher selects `.venv/bin/python` when it has the `mcp` package installed,
then falls back to another Python with `mcp` available. If dependencies are
missing, it prints the venv setup commands before exiting.

### Codex CLI Warden hooks

Direct Codex CLI sessions can use the same local Warden gates as Claude Code:

```bash
python3 scripts/codex_warden_hooks.py install --dry-run
python3 scripts/codex_warden_hooks.py install
python3 scripts/codex_warden_hooks.py check
```

The installer writes user-local Codex config only: `~/.codex/hooks.json` and
`~/.codex/config.toml`. It merges with unrelated hooks, writes backups before
replacement, and enables `[features].codex_hooks = true`. Pass
`--python <command>` if the default hook interpreter is not right for the
machine. Pass `--script-path <path>` when installing hooks from a stable copy
of `scripts/codex_warden_hooks.py` while managing a different repo root; the
installed commands and generated approval commands use that script path.

`python3 scripts/codex_warden_hooks.py check` prints every managed behavior,
its event/matcher, whether it is installed, the script path, and the feature
flag state. Set `DEUS_CODEX_HOOK_DEBUG=1` to write non-blocking hook diagnostics
to `~/.deus/codex_warden_hooks.log`.

To remove only this repo's managed hooks:

```bash
python3 scripts/codex_warden_hooks.py uninstall
```

Known Codex hook parity gaps are tracked in
[agent-agnostic-debt.md](agent-agnostic-debt.md).

The hooks also block `gh pr merge --admin` unless the exact command has fresh
explicit approval. A normal "merge after CI" approval does not approve bypassing
branch policy.

Codex mirrors the current Claude Code hooks as closely as Codex hook events
allow: session marker reset, stop checkpoint forwarding, plan-review and
code-review gates, plan-mode invalidation, memory-tree re-embedding,
threat-model and path-leak warnings, catch-up freshness injection, memory
retrieval, and an opt-in public-safe orchestrator preflight. Remaining gaps are
tracked in [agent-agnostic-debt.md](agent-agnostic-debt.md): plan-mode
invalidation is approximate, Stop checkpointing needs live Codex transcript
verification, and the private Claude orchestrator preflight is default-off in
public Codex hooks.

## Backend Management

Manage the default backend and model from the command line:

```bash
deus backend              # Show current backend and model
deus backend show         # Same as above
deus backend list         # List available backends with active marker
deus backend set codex    # Set default backend
deus backend model gpt-4o # Set model for current backend
```

Changes persist to `~/.config/deus/config.json` (user preferences) and `.env` (service runtime). They take effect on the next `deus` launch.

### Precedence

The backend is resolved in this order (first non-empty wins):

1. **Per-session prefix** — `deus codex` / `deus claude` (env vars for this process)
2. **Environment variable** — `DEUS_AGENT_BACKEND` in `.env` or shell
3. **User config** — `agent_backend` in `~/.config/deus/config.json` (set via `deus backend set`)
4. **Default** — `claude`

`deus backend set` writes to both config.json and `.env` so both the CLI and background service pick up the change.

## llama.cpp local backend

llama.cpp is a third backend that runs as a local `llama-server` HTTP service on the host. The container talks to it via the OpenAI-compatible `/v1/chat/completions` endpoint — no API key, no credential proxy hop, no per-turn cost.

### Setup

1. Run the `/add-llama-cpp` skill on the host to install `llama-server` and configure the LaunchAgent (macOS) or run it manually (Linux/Windows). See [`.claude/skills/add-llama-cpp/SKILL.md`](../.claude/skills/add-llama-cpp/SKILL.md).
2. Confirm the local endpoint: `curl -fsS http://127.0.0.1:8080/v1/models`.
3. **Rebuild the agent container** if upgrading from a pre-llama-cpp Deus build: `./container/build.sh`. Without this, the container won't have the new `llama-cpp-backend.js` module.
4. Switch backend: `deus backend set llama-cpp`.
5. Trigger a session: send a channel message, schedule a task, or set `agent_backend: 'llama-cpp'` on a specific group/task.
6. For an interactive REPL (`deus`), use Claude or Codex — `deus` foreground TUI for llama-cpp is a follow-up (PR #6).

**Scope of this integration:** chat-backend only. Eval-side providers (text generation for the evolution harness, the local judge, and embedding) are tracked as follow-ups per ADR `docs/decisions/llama-cpp-optional-integration.md`. The embedding swap in particular is gated on a full re-embed + threshold recalibration + benchmark snapshot.

### `LLAMA_CPP_PORT` precedence

`process.env.LLAMA_CPP_PORT > .env file > '8080' default`. Keep this aligned with `~/.config/deus/llama-cpp.env` (which the skill writes). Default in both: `8080`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEUS_AGENT_BACKEND` | `claude` | Global default: `claude`, `openai`, or `llama-cpp` |
| `DEUS_OPENAI_MODEL` | `gpt-4o` | Model for the OpenAI backend |
| `OPENAI_API_KEY` | -- | Required unless Codex OAuth is available (`~/.codex/auth.json` from `codex login`) |
| `DEUS_CODEX_MODEL` | `DEUS_OPENAI_MODEL` | Optional model override for `deus codex` |
| `LLAMA_CPP_BASE_URL` | -- | Optional explicit host-side llama-server URL (advanced) |
| `LLAMA_CPP_PORT` | `8080` | Port that `llama-server` listens on the host |
| `LLAMA_CPP_MODEL` | -- | GGUF model alias to send in `chat/completions` requests |

## Supported Backend Boundary

Three implemented agent backends: Claude (default), OpenAI/Codex (opt-in via API key or Codex OAuth), and llama.cpp (opt-in via the `/add-llama-cpp` skill). The `ollama` entry in the `AgentRuntimeId`-style CLI display alias is a forward reservation with no runtime implementation — Ollama is used for eval judging and embeddings, not as a container agent backend.

## Adding a New Backend (for contributors)

1. Create a factory function in `src/agent-runtimes/` (see `claude-backend.ts` or `llama-cpp-backend.ts` as templates)
2. Define capabilities in a `RuntimeCapabilities` constant
3. Register it in `src/index.ts` via `registry.register(createYourRuntime(deps))`
4. Add container-side dispatch in `container/agent-runner/src/index.ts`
5. Add the backend name to `AgentRuntimeId` union in `src/agent-runtimes/types.ts` AND to `VALID_BACKENDS` in `container/agent-runner/src/tool-broker.ts` (container-side single source of truth)
6. Update `parseAgentBackend()` in `src/agent-runtimes/types.ts` (host SoT) — used by `src/config.ts` `DEFAULT_AGENT_RUNTIME` and `src/db.ts` `rowToSessionRef`
7. If the new backend bypasses the credential proxy (like llama.cpp), restructure the parity test suite in `src/container-runner.test.ts` into shared-env / remote-proxy / local-bypass tiers
