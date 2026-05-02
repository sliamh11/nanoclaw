# Deus TUI

Ratatui-based terminal interface for Deus that wraps `claude -p` and
`codex exec` behind a unified chat UI. Ships as a single static binary
with panels for wardens, services, channels, config, and system status.

## Architecture

```
src/
  main.rs          Event loop, terminal setup, key dispatch
  app.rs           State machine (App struct, tabs, chat, commands)
  ui.rs            Rendering — draws frames from App state
  backend/
    mod.rs         Backend trait + model registry
    claude.rs      Claude provider (claude -p --output-format stream-json)
    codex.rs       Codex provider (codex exec)
  config/
    mod.rs         Repo root detection
    wardens.rs     Warden entries from .claude/wardens/
    healthcheck.rs Service health from healthcheck.json
    channels.rs    Channel status
    deus.rs        General config from ~/.config/deus/config.json
  panels/
    chat.rs        Chat panel rendering
    wardens.rs     Warden list + toggle
    services.rs    Service health dashboard
    channels.rs    Channel status list
    config.rs      Config key/value display
    status.rs      System dashboard
  widgets/         Reusable TUI widgets
  platform.rs      OS abstraction (paths, env vars, platform detection)
  theme.rs         Brand color palette (Ember/Ocean/Teal)
  bidi.rs          Bidirectional text support
```

## Adding a New Backend

1. Create `src/backend/yourbackend.rs` implementing the `Backend` trait:
   - `name()` / `display_name()` -- identifier and label
   - `models()` -- return `&'static [ModelDef]` with id, display name, context
   - `build_command()` -- construct the CLI `Command` from `RunConfig`
   - `parse_line()` -- parse stdout JSON lines into `Vec<StreamChunk>`
2. Add `pub mod yourbackend;` to `src/backend/mod.rs`.
3. Add `Box::new(yourbackend::YourBackend)` to `all_backends()` in
   `src/backend/mod.rs`.

Model routing is automatic -- `find_backend()` matches on model ID.

## Key Bindings

### Chat Panel

| Key              | Action                         |
|------------------|--------------------------------|
| Enter            | Send message / accept suggestion |
| Shift+Enter      | Insert newline                 |
| Ctrl+J           | Insert newline                 |
| Esc              | Dismiss suggestions / cancel stream / double-tap to quit |
| Ctrl+C           | Cancel stream (or quit if idle) |
| Ctrl+D           | Quit                           |
| Ctrl+L           | Clear chat                     |
| Ctrl+U           | Clear input line               |
| Ctrl+A / Ctrl+E  | Home / End                     |
| Ctrl+W / Alt+Bksp| Delete word                    |
| Ctrl+K           | Kill to end of line            |
| Ctrl+Y           | Yank (paste kill ring)         |
| Ctrl+O           | Toggle tool visibility         |
| Alt+B / Alt+F    | Word left / right              |
| Up / Down        | History prev/next or suggestion nav |
| PageUp / PageDn  | Scroll chat history            |
| Cmd+Backspace    | Delete current line (macOS)    |
| Tab              | Accept suggestion              |

### Panel Navigation

| Key              | Action                         |
|------------------|--------------------------------|
| Esc / q          | Return to Chat                 |
| j / Down         | Next item                      |
| k / Up           | Previous item                  |
| Space / Enter    | Toggle item                    |
| r                | Refresh panel data             |

Panels are opened via slash commands: `/wardens`, `/services`, `/channels`,
`/config`, `/status`.

## Configuration

The TUI reads `~/.config/deus/config.json` for general settings (displayed in
the Config panel). Backend and behavior are controlled via environment
variables:

| Variable                  | Default   | Description                        |
|---------------------------|-----------|------------------------------------|
| `DEUS_TUI_BACKEND`       | `claude`  | Default backend (`claude` or `codex`) |
| `DEUS_TUI_MODE`          | `home`    | Operating mode                     |
| `DEUS_TUI_CONTEXT_FILE`  | (none)    | Path to system context file        |
| `DEUS_TUI_BYPASS`        | `false`   | Bypass permission prompts          |
| `DEUS_TUI_INITIAL_PROMPT`| (none)    | Auto-send a prompt on startup      |

When `DEUS_TUI_BACKEND=codex`, the default model switches to `gpt-5.4`;
otherwise it defaults to `sonnet`. Models can be changed at runtime via
`/model <id>`.

If stdout is not a terminal, the TUI prints a static dashboard and exits.
