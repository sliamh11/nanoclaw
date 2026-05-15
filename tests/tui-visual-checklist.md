# TUI Visual Verification Checklist

One-time checklist to verify TUI visual states that automated tests cannot
cover. Created to close the chronic deferral from RETRO-2026-05-16-03.
Source line numbers are snapshots from 2026-05-16; see the commit for exact
state.

## How to use

Build the TUI binary, then work through each item. Mark the checkbox when the
visual state matches the expected outcome.

```bash
cd ~/deus/tui && cargo build --release
```

---

- [ ] **Static dashboard (non-TTY)**
  - **Trigger:** `./tui/target/release/deus-tui < /dev/null`
  - **Expected:** A 72-character-wide boxed table prints to stdout with four
    sections: WARDENS, SERVICES, CHANNELS, CONFIG. Each section has a header
    and rows with status icons (checkmark/cross).
  - **Pass:** Box borders render without corruption, sections are present and
    labeled, output fits in a standard 80-column terminal.
  - **Source:** `tui/src/main.rs:468–546`

- [ ] **Braille logo and welcome screen**
  - **Trigger:** Launch `./tui/target/release/deus-tui` with no prior session
    (empty chat history).
  - **Expected:** A braille-art logo renders in four brand colors
    (Ember #E8723A, Flame #F4A261, Ocean #2EC4B6, Deep Teal #1B7A6E),
    followed by "D  E  U  S" in accent color and a version string in dim
    text. Below: "Type a message or / for commands."
  - **Pass:** Logo characters are visible (not blank boxes), colors are
    distinct (not all white or all one color), version string is present.
  - **Source:** `tui/src/logo.rs`, `tui/src/panels/chat.rs:235–242`

- [ ] **Bypass permissions status bar label**
  - **Trigger:** Launch with `DEUS_TUI_BYPASS=true ./tui/target/release/deus-tui`.
  - **Expected:** The status bar at the bottom shows "bypass" in orange/warn
    color, not the raw string "bypassPermissions".
  - **Pass:** The word "bypass" appears in the status bar in a visually
    distinct warm color (orange). The full internal mode name
    "bypassPermissions" does not appear anywhere in the UI.
  - **Source:** `tui/src/ui.rs:66–77`

- [ ] **Context gauge color thresholds**
  - **Trigger:** Send messages until token usage increases. Observe the
    `[||||......] XX%` gauge on the right side of the status bar.
  - **Expected:** The gauge is green when usage is below 50%, yellow/orange
    when between 50% and 74%, and red when 75% or above.
  - **Pass:** At least two color transitions are visually confirmed as usage
    increases. The gauge never shows an incorrect color for its percentage.
  - **Source:** `tui/src/ui.rs:161–167`

- [ ] **RTL text visual reorder**
  - **Trigger:** Type a message containing Hebrew text (e.g., "hello שלום
    world") and send it.
  - **Expected:** The Hebrew characters render in right-to-left visual order
    within the message bubble. The surrounding English text remains
    left-to-right.
  - **Pass:** Hebrew glyphs are not garbled or reversed at the character
    level. The mixed-direction line reads naturally (English LTR, Hebrew
    RTL).
  - **Source:** `tui/src/bidi.rs`, `tui/src/panels/chat.rs:103`

- [ ] **Syntax-highlighted code blocks with diff coloring**
  - **Trigger:** Ask the agent to produce a code block (e.g., "show me a
    short Rust function") and a diff block (e.g., "show a git diff example").
  - **Expected:** Code blocks show per-token syntax highlighting with colors
    matching the base16-ocean.dark theme. Diff blocks show green for added
    lines (`+`), red for removed lines (`-`), and accent color for hunk
    headers (`@@`).
  - **Pass:** Code block text is not monochrome — at least keywords, strings,
    and identifiers are visually distinct. Diff `+`/`-` lines use
    green/red respectively.
  - **Source:** `tui/src/highlight.rs`, `tui/src/panels/chat.rs:137–160`

- [ ] **Markdown table column alignment**
  - **Trigger:** Ask the agent to produce a markdown table with at least 3
    columns and varying cell widths.
  - **Expected:** Columns are padded to equal width within each column. The
    header row is bold. A `─┼─` separator line appears between the header
    and body rows. Column separators use `│`.
  - **Pass:** Columns are visually aligned (no ragged edges). The header is
    visually distinct from body rows.
  - **Source:** `tui/src/panels/chat.rs:347–414`
