---
governs:
  - src/container-mounter.ts
  - src/credential-proxy.ts
  - src/ipc.ts
---
# Pattern: security-review

## Trust model

| Entity | Trust level |
|--------|-------------|
| Main group | Trusted — admin control |
| Non-main groups | Untrusted — treat as hostile input |
| Container agents | Sandboxed — isolated execution |
| Channel messages | User input — potential prompt injection |

## Mount security

- Mount allowlist lives at `~/.config/deus/mount-allowlist.json` — outside project root, never mounted into containers, cannot be modified by agents.
- Symlinks resolved before validation (prevents traversal attacks).
- Container paths reject `..` and absolute paths.
- `.env` is shadowed with `/dev/null` in the project root mount.

## Credential isolation

Real credentials **never enter containers**. The credential proxy at `:3001` injects auth headers transparently. Containers receive `ANTHROPIC_API_KEY=placeholder` and `ANTHROPIC_BASE_URL=http://host.docker.internal:<port>`.

Never committed / never mounted:
- `store/auth/` — WhatsApp session
- `~/.config/deus/mount-allowlist.json`
- Any file matching blocked patterns (`.ssh`, `.env`, `credentials`, `private_key`, etc.)

## IPC authorization

Non-main groups: cannot send to other chats, cannot schedule tasks for others, view own tasks only. Verify group identity before any privileged operation.

## Security audit checklist

```
[ ] No credentials in code, test files, or git history
[ ] New credentials have .env.example entry with descriptive comment
[ ] IPC operations verify group identity
[ ] Container mounts go through allowlist validation
[ ] No secrets in container environment or mounted paths
```

## Extra doc

Load `docs/SECURITY.md` for the full trust table, privilege comparison, and architecture diagram.
