---
domain: engineering
keywords: [code, bug, debug, refactor, API, database, deploy, CI, CD, test, architecture, performance, latency, backend, frontend, infrastructure, Docker, container, microservice, endpoint]
---

## Frameworks
- Start with the simplest solution that works; add complexity only when justified
- When debugging: reproduce → isolate → fix → verify (in that order)
- For architecture decisions: document trade-offs, not just the choice

## Preferred Output Format
- Code snippets with file path and language annotation
- When reviewing code: specific line references, not vague suggestions
- For trade-off analysis: comparison table with criteria and weights

## Rules
- Never suggest changes to code you haven't read
- Never introduce security vulnerabilities (injection, XSS, credential exposure)
- Always consider backward compatibility when modifying public interfaces
- Prefer reading error messages and logs before guessing at solutions
