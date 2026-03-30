# Benchmark Comparison

How Deus compares to other open-source AI assistant frameworks. Last updated: March 2026.

## Philosophy

Different tools solve different problems. **OpenClaw** optimizes for breadth: 10+ channels, 5,700+ community skills, any LLM provider. **Deus** optimizes for depth: semantic memory that actually recalls last week's conversation, a self-improvement loop that tunes its own prompts, and per-conversation container isolation that's on by default — not opt-in.

Choose the right tool for what you care about.

---

## Feature Comparison

|  | **Deus** | **[OpenClaw](https://github.com/openclaw/openclaw)** | **[NemoClaw](https://github.com/NVIDIA/NemoClaw)** | **[ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw)** | **Plain Claude** |
|---|---|---|---|---|---|
| **Messaging channels** | 4 (WhatsApp, Telegram, Slack, Discord) | 10+ (WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Teams, IRC...) | Via OpenClaw | 20+ | None |
| **Agent isolation** | Linux container per conversation (default) | Opt-in Docker sandbox | Landlock + seccomp + namespaces | Rust process sandbox | None |
| **Memory** | Semantic vector DB (sqlite-vec) + tiered retrieval (warm + cold) | Markdown files on disk | Via OpenClaw | Built-in persistence | Conversation window only |
| **Self-improvement** | Judge → reflexion → DSPy prompt optimization | No | No | No | No |
| **Eval / CI layer** | DeepEval test suite (QA, tool use, safety) | No | No | No | No |
| **Credential isolation** | Proxy injects keys at runtime; containers never see real credentials | Keys in environment | Policy-controlled | Keys in environment | N/A (cloud) |
| **LLM support** | Claude only | Any (OpenAI, Anthropic, Ollama, local) | Any (via OpenClaw) | Any | Claude only |
| **Codebase size** | ~9,500 lines TypeScript | ~430,000 lines | Wrapper over OpenClaw | Single Rust binary | N/A |
| **Community** | New project | 250K+ GitHub stars, 5,700+ skills | NVIDIA-backed, alpha | 18K GitHub stars | N/A |
| **License** | MIT | MIT | Open source | Open source | Proprietary |
| **Self-hosted** | Yes (macOS, Linux) | Yes (any OS) | Yes (NVIDIA GPU preferred) | Yes (any OS) | No (cloud only) |

---

## Deep Dives

### Agent Isolation

Most frameworks treat sandboxing as optional. Deus makes it the default architecture — every conversation group gets its own Linux container with:

- **Non-root execution** (uid 1000, no privilege escalation)
- **Read-only source mounts** (agent can't modify the host codebase)
- **Credential proxy** (real API keys never enter the container — a host-side proxy injects them at the HTTP level)
- **Mount allowlist** (stored outside the project directory, tamper-proof from inside the container)
- **Per-group session isolation** (groups can't see each other's conversation history)

OpenClaw runs agents in the host process by default and offers Docker as an opt-in. NemoClaw adds kernel-level sandboxing (Landlock, seccomp) which is stronger at the syscall level, but doesn't isolate per-conversation — it's one sandbox for the whole agent.

### Memory Architecture

| System | Storage | Search | Cross-session recall |
|--------|---------|--------|---------------------|
| **Deus** | SQLite + sqlite-vec (768-dim vectors) | Semantic search with recency boost (7d: -0.3, 30d: -0.15 L2 adjustment) | Yes — tiered: recent sessions free, older sessions via embedding search |
| **OpenClaw** | Markdown files on disk | Keyword / filename | Session-based persistence, no semantic search |
| **NemoClaw** | Via OpenClaw | Via OpenClaw | Via OpenClaw |
| **ZeroClaw** | Built-in persistence | Unknown | Basic persistence |
| **Plain Claude** | Context window | None | No (each conversation is isolated) |

Deus uses a two-tier retrieval strategy:
- **Warm tier**: Last N sessions by date. No API call, no embedding cost. Free.
- **Cold tier**: Semantic search over all indexed sessions. One Gemini embedding call. Results are re-ranked by recency so recent context surfaces higher.

This means asking "what did we discuss about the auth migration?" will find the relevant session even if it was weeks ago and used different terminology.

### Self-Improvement Loop

Unique to Deus. No other framework in this comparison has an automated self-improvement pipeline.

```
Production interaction
        │
        ▼
   Judge scores it (0.0 – 1.0)
   ├── Ollama (local, free) for eval
   └── Gemini for production scoring
        │
        ▼
   Score < 0.6?
   ├── Yes → Reflexion: generate self-critique + corrected response
   │         Store for future pattern matching
   └── No  → Log and move on
        │
        ▼
   20+ scored samples accumulated?
   ├── Yes → DSPy optimizes the system prompt
   │         using scored interactions as training data
   └── No  → Wait for more data
```

The agent literally gets better at its job over time. Low-scoring responses generate reflections that improve future answers. Once enough data accumulates, DSPy tunes the system prompt itself.

### Eval Layer

Deus includes a [DeepEval](https://github.com/confident-ai/deepeval) test suite that validates agent behavior before merges:

- **Core Q&A tests** — factual accuracy, reasoning
- **Tool use tests** — correct tool selection, parameter generation
- **Safety tests** — refusal of harmful requests, prompt injection resistance
- **Parallel pre-warm** — containers cached across test runs
- **Dynamic concurrency** — scales to available resources

Judge options: OllamaJudge (local, free, qwen3.5:4b) or GeminiJudge (cloud).

No other framework in this comparison ships with a built-in eval suite for the agent's conversational behavior.

---

## When to Choose What

### Choose Deus if you want:
- An assistant that **remembers** — semantic memory that recalls conversations from weeks ago
- An assistant that **improves** — automated scoring, reflection, and prompt optimization
- **Security by default** — container isolation without configuration
- A **small, understandable codebase** — 9.5K lines you can read in an afternoon
- To run **Claude as your core agent** with deep SDK integration

### Choose OpenClaw if you want:
- **Maximum channel support** — 10+ platforms including Signal, iMessage, Teams
- **Any LLM provider** — switch between OpenAI, Anthropic, Ollama, local models
- A **massive community** — 250K stars, 5,700+ ready-made skills
- **Battle-tested at scale** — largest open-source project in this space

### Choose NemoClaw if you want:
- **Enterprise compliance** — NVIDIA-backed, policy-controlled sandboxing
- **Kernel-level security** — Landlock + seccomp syscall filtering
- The OpenClaw ecosystem with **enterprise guardrails**

### Choose ZeroClaw if you want:
- **Maximum performance** — 4MB RAM, <10ms boot, Rust-native
- **Maximum channel breadth** — 20+ platforms
- **Minimal resource footprint** — runs on embedded hardware

### Choose Plain Claude if you want:
- **No infrastructure** — just open claude.ai and talk
- **Zero setup cost** — no self-hosting, no containers, no configuration

---

## What We Don't Claim

- We don't claim to be better than OpenClaw at channel breadth or ecosystem size. OpenClaw has 250K stars for good reason.
- We don't claim model flexibility. Deus requires Claude. This is a deliberate trade-off for deep SDK integration (session management, MCP tools, agent orchestration).
- Quantitative latency/throughput comparisons are not included because they depend heavily on hardware, network, and API tier — not the framework itself.

---

## Contributing

Found an error in this comparison? [Open an issue](https://github.com/YOUR_USERNAME/deus/issues) or submit a PR. We want this to be accurate, not favorable.
