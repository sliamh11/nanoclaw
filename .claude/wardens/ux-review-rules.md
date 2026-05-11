# UX Review Rules — Wardens/ux-reviewer

> Rules the `ux-reviewer` agent checks against AFTER implementation.
> Add a new rule by appending a section. No agent edit needed.
>
> Format per rule: `Severity`, `Applies when`, `Check`, `Rule`, `Cite`.
> Severity: `critical` (users will be stuck or confused) · `major` (noticeable friction) · `minor` (polish).

## system-status-visibility
**Severity:** critical
**Applies when:** Change affects a state transition the user can trigger (sending a message, approving a permission, starting an agent, queueing).
**Check:** Can the user always tell what state the system is in? Is there a spinner, status indicator, or feedback for every async operation?
**Rule:** Every user action must produce visible feedback within 100ms. Long operations need progress indication.
**Cite:** Nielsen H1 (Visibility of system status)

## input-overflow-handling
**Severity:** major
**Applies when:** Change affects any text input field or display area.
**Check:** What happens when content exceeds the visible area? Is there scroll, truncation, or silent clipping?
**Rule:** Overflowing content must be scrollable or gracefully truncated with an indicator — never silently clipped.
**Cite:** Nielsen H1 + edge-case methodology

## error-state-recovery
**Severity:** critical
**Applies when:** Change introduces a new failure mode or modifies error handling.
**Check:** When an error occurs, does the user know what happened, why, and what to do next? Can they recover without restarting?
**Rule:** Error messages must explain the problem in user terms and suggest a recovery action. Never show raw errors without context.
**Cite:** Nielsen H9 (Help users recognize, diagnose, and recover from errors)

## keyboard-discoverability
**Severity:** major
**Applies when:** Change adds or modifies keyboard shortcuts or interaction patterns.
**Check:** Can a user discover the shortcut without reading docs? Is it shown in the UI (status bar, hint text, help)?
**Rule:** Every keyboard shortcut must be discoverable from the UI itself. Hidden shortcuts are dead shortcuts.
**Cite:** Nielsen H6 (Recognition rather than recall)

## consistency-across-surfaces
**Severity:** major
**Applies when:** Change modifies behavior that exists in multiple channels (TUI, WhatsApp, Telegram, Slack, CLI).
**Check:** Does the same concept (permissions, queuing, error display) work the same way across channels?
**Rule:** Same concept = same behavior. If a channel must differ, the difference should be the minimum needed and documented.
**Cite:** Nielsen H4 (Consistency and standards)

## information-density
**Severity:** minor
**Applies when:** Change adds new UI elements, status indicators, or message formatting.
**Check:** Is every visible element earning its screen space? Is there clutter that could be hidden behind a toggle or removed?
**Rule:** Default to showing less. Additional detail should be opt-in (toggles, expand, hover) not opt-out.
**Cite:** Nielsen H8 (Aesthetic and minimalist design)

## rtl-bidi-support
**Severity:** major
**Applies when:** Change affects text rendering in any display area (messages, input, status).
**Check:** Does mixed RTL/LTR text (Hebrew+English) render correctly? Are punctuation and numbers positioned correctly at script boundaries?
**Rule:** BiDi text must render in correct visual order. Test with a mixed Hebrew+English sentence containing numbers and punctuation.
**Cite:** Deus user profile (Hebrew speaker); terminal BiDi limitation

## first-use-experience
**Severity:** major
**Applies when:** Change introduces a new feature, panel, command, or interaction pattern.
**Check:** What does a user see the first time they encounter this? Is there any guidance, or is it a blank screen / cryptic UI?
**Rule:** New features need at minimum a placeholder or hint text on first encounter. Don't assume the user read a changelog.
**Cite:** Nielsen H10 (Help and documentation)

## destructive-action-safety
**Severity:** critical
**Applies when:** Change enables deletion, clearing, overwriting, or any irreversible action.
**Check:** Is there a confirmation step? Can the user undo? Is it clear what will be lost?
**Rule:** Destructive actions require confirmation and should be undoable when possible. The confirmation must name what's being destroyed.
**Cite:** Nielsen H3 (User control and freedom); Deus no-db-deletion policy

## text-selection-copy
**Severity:** major
**Applies when:** Change affects mouse handling, terminal capture, or clipboard integration.
**Check:** Can the user select and copy text from the output? Is the mechanism discoverable (e.g., Shift+drag hint)?
**Rule:** Users must be able to copy output text. If mouse capture prevents native selection, provide an alternative and make it discoverable.
**Cite:** Nielsen H7 (Flexibility and efficiency of use)

## permission-prompt-clarity
**Severity:** critical
**Applies when:** Change affects permission prompts, approval flows, or trust decisions.
**Check:** Does the user have enough information to make an informed decision? Is the full command/action visible? Are the options clear?
**Rule:** Permission prompts must show the full action being approved (not truncated) and clearly label each option.
**Cite:** Security UX best practices; Deus permission bridge design

## queue-and-async-feedback
**Severity:** major
**Applies when:** Change affects message queuing, background agents, or any deferred action.
**Check:** Does the user know their action was received? Can they see what's queued? Do they know when it will execute?
**Rule:** Queued/deferred actions must show the actual content (not just "queued"), position in queue, and update when status changes.
**Cite:** Nielsen H1 (Visibility of system status)

## scroll-and-viewport
**Severity:** major
**Applies when:** Change affects scrollable areas (chat messages, input field, suggestion lists).
**Check:** Is the scroll position intuitive? Does new content auto-scroll only when the user is at the bottom? Can the user scroll back without fighting auto-scroll?
**Rule:** Auto-scroll when pinned to bottom; preserve position when user has scrolled up. New content should not fight the user's scroll position.
**Cite:** Chat UX conventions (Slack, Discord, iMessage pattern)
