# QA Test Rules — Wardens/qa-tester

> Rules the `qa-tester` agent checks against AFTER implementation.
> Add a new rule by appending a section. No agent edit needed.
>
> Format per rule: `Severity`, `Applies when`, `Check`, `Rule`, `Cite`.
> Severity: `critical` (untested path causes data loss or security issue) · `major` (untested path causes wrong behavior) · `minor` (edge case unlikely to hit production).

## happy-path-coverage
**Severity:** critical
**Applies when:** Any new function, endpoint, handler, or user-facing flow is added or modified.
**Check:** Does a test exist that exercises the normal input → expected output path? Does the assertion verify the output value, not just "no error thrown"?
**Rule:** Every user-reachable code path must have at least one test that asserts the correct output for valid input. "No crash" assertions are insufficient.
**Cite:** Test pyramid principles (unit base); Deus CI requirements

## edge-case-identification
**Severity:** major
**Applies when:** Change accepts external input (user messages, API responses, file contents, environment variables, command-line arguments).
**Check:** Are these inputs tested: empty string, null/undefined, boundary values (0, -1, MAX_INT), malformed format, unexpected type, extremely long input, special characters (newlines, tabs, null bytes)?
**Rule:** External input handlers must be tested with at least: empty input, malformed input, and boundary values. Each edge case needs its own test case with a descriptive name.
**Cite:** OWASP input validation; boundary value analysis methodology

## regression-risk
**Severity:** major
**Applies when:** Change modifies an existing function's signature, return type, side effects, or error behavior.
**Check:** Are the callers of the modified function tested? If a function's contract changed (new parameter, different error type, changed return shape), do downstream consumers have tests that would catch breakage?
**Rule:** When modifying a function's contract, verify that callers have tests covering the changed behavior. If callers lack tests, flag the regression risk with the specific call sites.
**Cite:** Regression testing principles; Deus no-data-loss policy

## cross-platform-scenarios
**Severity:** major
**Applies when:** Change uses filesystem paths, child processes, environment variables, shell commands, terminal escape sequences, or OS-specific APIs.
**Check:** Are platform differences tested or at minimum acknowledged? Path separators (`/` vs `\`), line endings (`\n` vs `\r\n`), case sensitivity, shell quoting, signal handling (SIGTERM vs process.kill).
**Rule:** Platform-dependent code must either be tested on multiple platforms in CI or use platform-agnostic abstractions with tests that verify the abstraction layer.
**Cite:** Deus cross-platform default rule (core-behavioral-rules.md)

## integration-boundaries
**Severity:** major
**Applies when:** Change touches an external system — API calls, database operations, file I/O, container interactions, MCP server communication, WebSocket connections.
**Check:** Is the boundary mocked in unit tests? Is there at least one integration test that exercises the real boundary (or a realistic fake)? Are error responses from the external system tested (timeout, 500, rate limit, auth failure)?
**Rule:** External system boundaries need both: (1) unit tests with mocked boundary for logic testing, and (2) integration tests or realistic fakes for contract verification. Error responses from the external system must be tested.
**Cite:** Contract testing principles; Deus container isolation architecture

## state-management
**Severity:** critical
**Applies when:** Change involves state machines, session management, caching, queues, or any mutable shared state.
**Check:** Are all state transitions tested? Is the initial state verified? Is cleanup/teardown tested? What happens on: interrupted transition, duplicate transition, invalid transition, concurrent access to the same state?
**Rule:** State-managing code must test: initial state, every valid transition, at least one invalid transition, cleanup on success, and cleanup on failure. Concurrent access must be addressed (tested or documented as single-threaded).
**Cite:** State machine testing methodology; Deus no-data-loss policy

## bidi-i18n-tests
**Severity:** minor
**Applies when:** Change handles text rendering, message formatting, string parsing, or display layout.
**Check:** Is RTL text tested (Hebrew, Arabic)? Mixed-direction text (Hebrew + English + numbers)? Unicode edge cases (emoji, ZWJ sequences, combining characters, surrogate pairs)? String length calculations that might break on multi-byte characters?
**Rule:** Text-handling code must include at least one test with RTL text and one with mixed-direction text. String length/slicing operations must be tested with multi-byte characters.
**Cite:** Deus user profile (Hebrew speaker); Unicode BiDi algorithm (UAX #9)
