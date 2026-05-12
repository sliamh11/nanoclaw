use std::cell::RefCell;

use ratatui::prelude::*;
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Wrap};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::app::{App, COMMANDS, MessageBlock, SessionId, SubagentStatus};
use crate::bidi;
use crate::platform;
use crate::theme;

thread_local! {
    static LINE_CACHE: RefCell<(SessionId, u64, Vec<Line<'static>>, Vec<String>)> = const { RefCell::new((SessionId::MAIN, u64::MAX, Vec::new(), Vec::new())) };
}

pub fn current_code_blocks() -> Vec<String> {
    LINE_CACHE.with(|cache| cache.borrow().3.clone())
}

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    let input_height = input_panel_height(app, area.width, area.height);
    let layout =
        Layout::vertical([Constraint::Min(0), Constraint::Length(input_height)]).split(area);

    render_messages(frame, app, layout[0]);
    render_input(frame, app, layout[1]);

    if app.has_suggestions() {
        render_suggestions(frame, app, layout[1]);
    }
}

fn parse_inline_markdown(text: &str) -> Vec<Span<'static>> {
    let bytes = text.as_bytes();
    let len = bytes.len();
    let mut spans: Vec<Span<'static>> = Vec::new();
    let mut pos = 0;
    let mut plain_start = 0;

    while pos < len {
        if pos + 1 < len
            && bytes[pos] == b'*'
            && bytes[pos + 1] == b'*'
            && let Some(end) = text[pos + 2..].find("**")
        {
            if pos > plain_start {
                spans.push(Span::raw(text[plain_start..pos].to_string()));
            }
            spans.push(Span::styled(
                text[pos + 2..pos + 2 + end].to_string(),
                theme::bold(),
            ));
            pos = pos + 2 + end + 2;
            plain_start = pos;
            continue;
        }
        if bytes[pos] == b'`'
            && let Some(end) = text[pos + 1..].find('`')
        {
            if pos > plain_start {
                spans.push(Span::raw(text[plain_start..pos].to_string()));
            }
            spans.push(Span::styled(
                text[pos + 1..pos + 1 + end].to_string(),
                theme::code(),
            ));
            pos = pos + 1 + end + 1;
            plain_start = pos;
            continue;
        }
        if bytes[pos] == b'['
            && let Some(bracket_end) = text[pos + 1..].find("](")
        {
            let label_end = pos + 1 + bracket_end;
            if let Some(paren_end) = text[label_end + 2..].find(')') {
                if pos > plain_start {
                    spans.push(Span::raw(text[plain_start..pos].to_string()));
                }
                let label = &text[pos + 1..label_end];
                let url = &text[label_end + 2..label_end + 2 + paren_end];
                spans.push(Span::styled(label.to_string(), theme::link_text()));
                spans.push(Span::styled(format!(" ({})", url), theme::link_url()));
                pos = label_end + 2 + paren_end + 1;
                plain_start = pos;
                continue;
            }
        }
        pos += 1;
    }
    if plain_start < len {
        spans.push(Span::raw(text[plain_start..].to_string()));
    }
    spans
}

fn render_markdown_line(
    text: &str,
    in_code_block: bool,
    in_diff: bool,
) -> (Line<'static>, bool, bool) {
    let mut code_block = in_code_block;
    let mut diff = in_diff;
    let text = bidi::visual_reorder(text);

    if text.starts_with("```") {
        let lang = text.trim_start_matches('`').trim();
        if code_block || diff {
            code_block = false;
            diff = false;
            return (
                Line::from(Span::styled("  ───", theme::muted())),
                code_block,
                diff,
            );
        }
        if lang == "diff" {
            diff = true;
            return (
                Line::from(Span::styled("  ─── diff", theme::muted())),
                code_block,
                diff,
            );
        }
        code_block = true;
        let label = if lang.is_empty() {
            "───".to_string()
        } else {
            format!("─── {}", lang)
        };
        return (
            Line::from(Span::styled(format!("  {}", label), theme::muted())),
            code_block,
            diff,
        );
    }

    if diff {
        let style = if text.starts_with('+') {
            theme::diff_add()
        } else if text.starts_with('-') {
            theme::diff_del()
        } else if text.starts_with("@@") {
            theme::diff_hunk()
        } else {
            theme::dim()
        };
        return (
            Line::from(Span::styled(format!("  │ {}", text), style)),
            code_block,
            diff,
        );
    }

    if code_block {
        return (
            Line::from(Span::styled(format!("  │ {}", text), theme::code())),
            code_block,
            diff,
        );
    }

    if text.contains("**") || text.contains('`') || text.contains("](") {
        let spans = parse_inline_markdown(&text);
        if spans.len() > 1 {
            let mut result = vec![Span::raw("  ".to_string())];
            result.extend(spans);
            return (Line::from(result), code_block, diff);
        }
    }

    if let Some(stripped) = text.strip_prefix("## ") {
        return (
            Line::from(Span::styled(format!("  {}", stripped), theme::heading2())),
            code_block,
            diff,
        );
    }
    if let Some(stripped) = text.strip_prefix("# ") {
        return (
            Line::from(Span::styled(format!("  {}", stripped), theme::heading1())),
            code_block,
            diff,
        );
    }

    if text.starts_with('>') {
        let depth = text.chars().take_while(|&c| c == '>').count().min(3);
        let rest = text[depth..].trim_start();
        let bar = "│ ".repeat(depth);
        return (
            Line::from(vec![
                Span::raw("  "),
                Span::styled(bar, theme::blockquote()),
                Span::raw(rest.to_string()),
            ]),
            code_block,
            diff,
        );
    }

    if let Some(dot_pos) = text.find('.')
        && dot_pos > 0
        && dot_pos <= 3
        && text[..dot_pos].chars().all(|c| c.is_ascii_digit())
        && text.get(dot_pos + 1..dot_pos + 2) == Some(" ")
    {
        let number = &text[..=dot_pos];
        let rest = &text[dot_pos + 2..];
        return (
            Line::from(vec![
                Span::raw("  "),
                Span::styled(format!("{} ", number), theme::bullet()),
                Span::raw(rest.to_string()),
            ]),
            code_block,
            diff,
        );
    }

    if text.starts_with("- ") || text.starts_with("* ") {
        return (
            Line::from(vec![
                Span::raw("  "),
                Span::styled("• ", theme::bullet()),
                Span::raw(text[2..].to_string()),
            ]),
            code_block,
            diff,
        );
    }

    (Line::from(format!("  {}", text)), code_block, diff)
}

fn render_welcome(lines: &mut Vec<Line<'static>>) {
    lines.extend(crate::logo::logo_lines());
    lines.push(Line::from(Span::styled(
        "      Type a message or / for commands.",
        theme::dim(),
    )));
    lines.push(Line::from(""));
}

fn render_subagent_block(
    lines: &mut Vec<Line<'static>>,
    app: &App,
    subagent_type: &str,
    description: &str,
    status: &SubagentStatus,
    output_preview: Option<&str>,
    is_warden: bool,
) {
    let (icon, icon_style, name_style) = match (is_warden, status) {
        (true, SubagentStatus::Running) => {
            let spinner = app.spinner_frame();
            (
                format!(" ⛨{} ", spinner),
                theme::warden_name(),
                theme::warden_name(),
            )
        }
        (true, SubagentStatus::Completed) => (
            " ⛨✓ ".to_string(),
            theme::verdict_ship(),
            theme::warden_name(),
        ),
        (false, SubagentStatus::Running) => {
            let spinner = app.spinner_frame();
            (
                format!(" ◈{} ", spinner),
                theme::agent_name(),
                theme::agent_name(),
            )
        }
        (false, SubagentStatus::Completed) => (
            " ◈✓ ".to_string(),
            Style::default().fg(theme::good_color()),
            theme::agent_name(),
        ),
    };

    let label = if is_warden { "Warden" } else { "Agent" };
    lines.push(Line::from(vec![
        Span::styled(icon, icon_style),
        Span::styled(format!("{} ({})", label, subagent_type), name_style),
        Span::styled(format!("  {}", description), theme::agent_detail()),
    ]));

    if *status == SubagentStatus::Completed
        && app.show_tools
        && let Some(preview) = output_preview
    {
        let max_lines = if is_warden { 10 } else { 5 };
        let total_lines = preview.lines().count();
        for (i, line) in preview.lines().take(max_lines).enumerate() {
            let text = if is_warden {
                highlight_verdict(line)
            } else {
                Line::from(vec![
                    Span::styled("   │ ", theme::muted()),
                    Span::raw(line.to_string()),
                ])
            };
            lines.push(text);
            if i == max_lines - 1 && total_lines > max_lines {
                lines.push(Line::from(Span::styled("   │ ⋯", theme::muted())));
            }
        }
    }
}

fn highlight_verdict(line: &str) -> Line<'static> {
    let trimmed = line.trim();
    if trimmed.contains("SHIP") {
        return Line::from(vec![
            Span::styled("   │ ", theme::muted()),
            Span::styled(line.to_string(), theme::verdict_ship()),
        ]);
    }
    if trimmed.contains("REVISE") {
        return Line::from(vec![
            Span::styled("   │ ", theme::muted()),
            Span::styled(line.to_string(), theme::verdict_revise()),
        ]);
    }
    if trimmed.contains("HOLD") {
        return Line::from(vec![
            Span::styled("   │ ", theme::muted()),
            Span::styled(line.to_string(), theme::verdict_hold()),
        ]);
    }
    Line::from(vec![
        Span::styled("   │ ", theme::muted()),
        Span::raw(line.to_string()),
    ])
}

fn extract_lang(fence_line: &str) -> Option<String> {
    let lang = fence_line.trim_start_matches('`').trim();
    if lang.is_empty() || lang == "diff" {
        None
    } else {
        Some(lang.to_string())
    }
}

fn flush_table(table_buffer: &mut Vec<String>, lines: &mut Vec<Line<'static>>) {
    if table_buffer.is_empty() {
        return;
    }
    let separator_idx = table_buffer
        .iter()
        .position(|row| row.contains("---") || row.contains("==="));
    let rows: Vec<Vec<String>> = table_buffer
        .iter()
        .enumerate()
        .filter(|(i, _)| Some(*i) != separator_idx)
        .map(|(_, row)| {
            row.trim_matches('|')
                .split('|')
                .map(|cell| cell.trim().to_string())
                .collect()
        })
        .collect();
    let col_count = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    let mut col_widths = vec![0usize; col_count];
    for row in &rows {
        for (j, cell) in row.iter().enumerate() {
            if j < col_count {
                col_widths[j] = col_widths[j].max(cell.len());
            }
        }
    }
    for (i, row) in rows.iter().enumerate() {
        let mut spans: Vec<Span<'static>> = vec![Span::raw("  ")];
        for (j, cell) in row.iter().enumerate() {
            let width = col_widths.get(j).copied().unwrap_or(cell.len());
            if j > 0 {
                spans.push(Span::styled(" │ ", theme::muted()));
            }
            spans.push(Span::raw(format!("{:<width$}", cell, width = width)));
        }
        let style = if i == 0 {
            theme::bold()
        } else {
            Style::default()
        };
        lines.push(Line::from(
            spans
                .into_iter()
                .map(|s| {
                    if i == 0 && s.style == Style::default() {
                        Span::styled(s.content, style)
                    } else {
                        s
                    }
                })
                .collect::<Vec<_>>(),
        ));
        if i == 0 {
            let sep: String = col_widths
                .iter()
                .map(|w| "─".repeat(*w))
                .collect::<Vec<_>>()
                .join("─┼─");
            lines.push(Line::from(Span::styled(
                format!("  {}", sep),
                theme::muted(),
            )));
        }
    }
    lines.push(Line::from(""));
    table_buffer.clear();
}

fn render_markdown_text(
    text: &str,
    lines: &mut Vec<Line<'static>>,
    code_blocks: &mut Vec<String>,
    current_block: &mut Option<String>,
) {
    let mut in_code = false;
    let mut in_diff = false;
    let mut highlighter: Option<crate::highlight::BlockHighlighter> = None;
    let mut table_buffer: Vec<String> = Vec::new();
    for line in text.lines() {
        if !in_code && !in_diff && line.starts_with('|') {
            table_buffer.push(line.to_string());
            continue;
        }
        if !table_buffer.is_empty() {
            flush_table(&mut table_buffer, lines);
        }

        let was_in_code = in_code || in_diff;
        let (mut rendered, new_code, new_diff) = render_markdown_line(line, in_code, in_diff);
        let is_heading =
            !in_code && !in_diff && (line.starts_with("# ") || line.starts_with("## "));
        let code_closed = was_in_code && !new_code && !new_diff;
        in_code = new_code;
        in_diff = new_diff;

        if !was_in_code && in_code {
            *current_block = Some(String::new());
            highlighter =
                extract_lang(line).and_then(|l| crate::highlight::BlockHighlighter::new(&l));
        } else if was_in_code && in_code {
            if let Some(ref mut h) = highlighter
                && let Some(spans) = h.highlight_line(line)
            {
                let mut highlighted = vec![Span::styled("  │ ", theme::muted())];
                highlighted.extend(spans);
                rendered = Line::from(highlighted);
            }
            if let Some(buf) = current_block.as_mut() {
                if !buf.is_empty() {
                    buf.push('\n');
                }
                buf.push_str(line);
            }
        } else if code_closed {
            highlighter = None;
            if let Some(buf) = current_block.take() {
                code_blocks.push(buf);
                let n = code_blocks.len();
                rendered = Line::from(vec![
                    Span::styled("  ───", theme::muted()),
                    Span::styled(format!(" [{}]", n), theme::muted()),
                ]);
            }
        }

        lines.push(rendered);
        if code_closed || is_heading {
            lines.push(Line::from(""));
        }
    }
    flush_table(&mut table_buffer, lines);
}

fn build_message_lines(app: &App) -> (Vec<Line<'static>>, Vec<String>) {
    let session = app.active();
    let mut lines: Vec<Line> = Vec::new();
    let mut code_blocks: Vec<String> = Vec::new();
    let mut current_block: Option<String> = None;

    for (msg_idx, msg) in session.chat_messages.iter().enumerate() {
        if msg.role == "system" && msg.content.starts_with("Welcome to Deus") {
            render_welcome(&mut lines);
            continue;
        }

        if msg.role == "system" && msg.content.starts_with("\u{203B} recap: ") {
            let summary = &msg.content["\u{203B} recap: ".len()..];
            lines.push(Line::from(vec![
                Span::styled("  \u{203B} ", theme::muted()),
                Span::styled("recap: ", theme::muted()),
                Span::styled(
                    summary.to_string(),
                    Style::default()
                        .fg(theme::text_dim_color())
                        .add_modifier(Modifier::ITALIC),
                ),
            ]));
            continue;
        }

        if msg.role == "assistant" && !msg.blocks.is_empty() {
            let mut last_thinking: Option<&str> = None;
            for block in &msg.blocks {
                if let MessageBlock::Thinking(text) = block {
                    last_thinking = Some(text);
                }
            }

            if app.show_tools
                && let Some(text) = last_thinking
            {
                let line_count = text.lines().count();
                for (i, tline) in text.lines().take(5).enumerate() {
                    let prefix = if i == 0 { " ⟡ " } else { "   " };
                    lines.push(Line::from(vec![
                        Span::styled(prefix, theme::muted()),
                        Span::styled(tline.to_string(), theme::thinking()),
                    ]));
                }
                if line_count > 5 {
                    lines.push(Line::from(Span::styled(
                        "   ⋯ (Ctrl+O to hide)",
                        theme::muted(),
                    )));
                }
            }

            for block in &msg.blocks {
                match block {
                    MessageBlock::Thinking(_) => {}
                    MessageBlock::ToolUse { tool, detail, .. } => {
                        if app.show_tools {
                            lines.push(Line::from(vec![
                                Span::styled(" ▸ ", Style::default().fg(theme::FLAME)),
                                Span::styled(tool.clone(), theme::tool_name()),
                                Span::styled(format!(" {}", detail), theme::tool_detail()),
                            ]));
                        }
                    }
                    MessageBlock::SubagentBlock {
                        subagent_type,
                        description,
                        status,
                        output_preview,
                        is_warden,
                        ..
                    } => {
                        render_subagent_block(
                            &mut lines,
                            app,
                            subagent_type,
                            description,
                            status,
                            output_preview.as_deref(),
                            *is_warden,
                        );
                    }
                    MessageBlock::Text(text) => {
                        render_markdown_text(
                            text,
                            &mut lines,
                            &mut code_blocks,
                            &mut current_block,
                        );
                    }
                }
            }
        } else if msg.role == "user" {
            for line in msg.content.lines() {
                let reordered = bidi::visual_reorder(line);
                lines.push(Line::from(vec![
                    Span::styled("▎", Style::default().fg(theme::OCEAN)),
                    Span::styled(format!(" {}", reordered), theme::user_msg()),
                ]));
            }
        } else {
            render_markdown_text(
                &msg.content,
                &mut lines,
                &mut code_blocks,
                &mut current_block,
            );
        }
        if msg_idx < session.chat_messages.len() - 1 {
            lines.push(Line::from(""));
        }
    }
    lines.push(Line::from(""));
    (lines, code_blocks)
}

fn highlight_search_matches(lines: Vec<Line<'static>>, query: &str) -> Vec<Line<'static>> {
    let query_lower = query.to_lowercase();
    lines
        .into_iter()
        .map(|line| {
            let full_text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
            let lower = full_text.to_lowercase();
            if !lower.contains(&query_lower) {
                return line;
            }
            let mut result_spans: Vec<Span<'static>> = Vec::new();
            let mut pos = 0usize;
            for (match_start, matched) in lower.match_indices(&query_lower) {
                let match_end = match_start + matched.len();
                if match_start > pos {
                    let end = match_start.min(full_text.len());
                    result_spans.push(Span::raw(full_text[pos..end].to_string()));
                }
                let ft_end = match_end.min(full_text.len());
                if match_start < full_text.len() {
                    result_spans.push(Span::styled(
                        full_text[match_start..ft_end].to_string(),
                        Style::default()
                            .bg(Color::Yellow)
                            .fg(Color::Black)
                            .add_modifier(Modifier::BOLD),
                    ));
                }
                pos = ft_end;
            }
            if pos < full_text.len() {
                result_spans.push(Span::raw(full_text[pos..].to_string()));
            }
            Line::from(result_spans)
        })
        .collect()
}

fn render_messages(frame: &mut Frame, app: &App, area: Rect) {
    let session = app.active();
    let is_streaming = matches!(session.chat_state, crate::app::ChatState::Streaming);

    let mut lines = if app.output_search_mode {
        let (built_lines, _) = build_message_lines(app);
        if !app.output_search_query.is_empty() {
            highlight_search_matches(built_lines, &app.output_search_query)
        } else {
            built_lines
        }
    } else {
        LINE_CACHE.with(|cache| {
            let mut cached = cache.borrow_mut();
            if cached.0 != app.active_session || cached.1 != session.chat_version {
                let (built_lines, blocks) = build_message_lines(app);
                cached.2 = built_lines;
                cached.3 = blocks;
                cached.0 = app.active_session;
                cached.1 = session.chat_version;
            }
            cached.2.clone()
        })
    };

    if is_streaming {
        let has_text = session
            .chat_messages
            .last()
            .is_some_and(|m| m.role == "assistant" && !m.content.is_empty());
        if !has_text {
            let spinner = app.spinner_frame();
            let thinking_text = session
                .last_thinking_summary
                .as_deref()
                .unwrap_or("thinking...");
            let preview: String = thinking_text.chars().take(60).collect();
            lines.push(Line::from(vec![
                Span::styled(format!(" {} ", spinner), theme::warn()),
                Span::styled(preview, theme::thinking()),
            ]));
        } else {
            lines.push(Line::from(vec![
                Span::styled(" ◇ ", theme::warn()),
                Span::styled("working...", theme::thinking()),
            ]));
        }
    }

    // Build permission block lines separately so they render as a fixed
    // widget at the bottom of the messages area, visible regardless of scroll.
    let perm_lines: Vec<Line> = if let Some(req) = session.pending_permissions.first() {
        let mut pl: Vec<Line> = Vec::new();
        pl.push(Line::from(""));
        let sep = "─".repeat(30);
        pl.push(Line::from(Span::styled(
            format!("  {} Permission Required {}", sep, sep),
            theme::warn(),
        )));
        pl.push(Line::from(""));
        pl.push(Line::from(vec![
            Span::styled("  Tool: ", theme::dim()),
            Span::styled(req.tool_name.clone(), theme::tool_name()),
        ]));
        if !req.tool_input_preview.is_empty() {
            for (i, input_line) in req.tool_input_preview.lines().enumerate() {
                if i >= 20 {
                    pl.push(Line::from(Span::styled("  │ ...", theme::muted())));
                    break;
                }
                let max_chars = (area.width as usize).saturating_sub(8);
                let truncated: String = input_line.chars().take(max_chars).collect();
                let display = if truncated.len() < input_line.len() {
                    format!("{}…", truncated)
                } else {
                    truncated
                };
                pl.push(Line::from(vec![
                    Span::styled("  │ ", theme::muted()),
                    Span::styled(display, theme::tool_detail()),
                ]));
            }
        }
        let pending_count = session.pending_permissions.len();
        if pending_count > 1 {
            pl.push(Line::from(""));
            pl.push(Line::from(Span::styled(
                format!("  ({} more pending)", pending_count - 1),
                theme::dim(),
            )));
        }
        pl.push(Line::from(""));
        pl.push(Line::from(vec![
            Span::styled("  Y", theme::accent_bold()),
            Span::styled(" allow  ", theme::dim()),
            Span::styled("N", theme::accent_bold()),
            Span::styled(" deny  ", theme::dim()),
            Span::styled("A", theme::accent_bold()),
            Span::styled(" always  ", theme::dim()),
            Span::styled("Esc", theme::accent_bold()),
            Span::styled(" deny", theme::dim()),
        ]));
        pl.push(Line::from(""));
        pl
    } else {
        Vec::new()
    };

    let perm_height = perm_lines.len() as u16;
    let msg_area = if perm_height > 0 {
        let chunks =
            Layout::vertical([Constraint::Min(0), Constraint::Length(perm_height)]).split(area);
        chunks[0]
    } else {
        area
    };

    let visible = msg_area.height as usize;
    let content_width = msg_area.width.max(1) as usize;
    let total: usize = lines
        .iter()
        .map(|line| {
            let w: usize = line
                .spans
                .iter()
                .map(|s| UnicodeWidthStr::width(s.content.as_ref()))
                .sum();
            if w == 0 { 1 } else { w.div_ceil(content_width) }
        })
        .sum();
    let max_scroll = total.saturating_sub(visible);
    let scroll = if session.scroll_pinned {
        max_scroll
    } else {
        max_scroll.saturating_sub(session.scroll_offset as usize)
    };

    let messages = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .scroll((scroll as u16, 0));
    frame.render_widget(messages, msg_area);

    if perm_height > 0 {
        let perm_area = Rect {
            x: area.x,
            y: area.y + area.height - perm_height,
            width: area.width,
            height: perm_height,
        };
        let perm_widget = Paragraph::new(perm_lines).wrap(Wrap { trim: false });
        frame.render_widget(Clear, perm_area);
        frame.render_widget(perm_widget, perm_area);
    }

    if app.output_search_mode {
        let match_info = if app.output_search_matches.is_empty() {
            if app.output_search_query.is_empty() {
                String::new()
            } else {
                " (no matches)".to_string()
            }
        } else {
            format!(
                " ({}/{})",
                app.output_search_current + 1,
                app.output_search_matches.len()
            )
        };
        let bar = Line::from(vec![
            Span::styled(" Search: ", theme::accent()),
            Span::raw(app.output_search_query.clone()),
            Span::styled(match_info, theme::dim()),
        ]);
        let bar_area = Rect {
            x: area.x,
            y: area.y + area.height.saturating_sub(1),
            width: area.width,
            height: 1,
        };
        frame.render_widget(Clear, bar_area);
        frame.render_widget(Paragraph::new(bar), bar_area);
    }
}

fn ghost_text(app: &App) -> String {
    if !app.input.starts_with('/') || app.input.is_empty() {
        return String::new();
    }

    if !app.arg_suggestions.is_empty() {
        if let Some(arg) = app.arg_suggestions.get(app.suggestion_cursor) {
            let space_idx = app.input.find(' ').unwrap_or(app.input.len());
            let typed_arg = &app.input[space_idx + 1..];
            if arg.starts_with(typed_arg) && arg.len() > typed_arg.len() {
                return arg[typed_arg.len()..].to_string();
            }
        }
        return String::new();
    }

    if let Some(&cmd_idx) = app.suggestions.get(app.suggestion_cursor) {
        let cmd = &COMMANDS[cmd_idx];
        if cmd.name.len() > app.input.len() {
            return cmd.name[app.input.len()..].to_string();
        }
    }

    String::new()
}

fn wrap_line_at_width(text: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut result: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut col = 0usize;

    for ch in text.chars() {
        let ch_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if col > 0 && col + ch_width > width {
            result.push(std::mem::take(&mut current));
            col = 0;
        }
        current.push(ch);
        col += ch_width;
    }
    result.push(current);
    result
}

fn wrapped_position(text: &str, width: usize) -> (usize, usize) {
    let segments = wrap_line_at_width(text, width);
    let row = segments.len().saturating_sub(1);
    let col: usize = segments
        .last()
        .map(|s| {
            s.chars()
                .map(|c| UnicodeWidthChar::width(c).unwrap_or(0))
                .sum()
        })
        .unwrap_or(0);
    (row, col)
}

struct InputLine {
    line: Line<'static>,
}

fn build_input_lines(app: &App, _content_width: usize) -> (Vec<InputLine>, usize, String) {
    let ghost = ghost_text(app);
    let cursor_line = app.input[..app.input_cursor].matches('\n').count();
    let cursor_text = app.input[..app.input_cursor]
        .rsplit('\n')
        .next()
        .unwrap_or("")
        .to_string();
    let segments: Vec<&str> = if app.input.is_empty() {
        vec![""]
    } else {
        app.input.split('\n').collect()
    };

    let mut lines = Vec::new();
    let mut text = String::new();
    for (i, segment) in segments.iter().enumerate() {
        let prefix = if i == 0 { " › " } else { " … " };

        if app.input.is_empty()
            && i == 0
            && matches!(app.active().chat_state, crate::app::ChatState::Idle)
        {
            lines.push(InputLine {
                line: Line::from(vec![
                    Span::styled(prefix, theme::accent_bold()),
                    Span::styled("Type a message or / for commands...", theme::muted()),
                ]),
            });
            continue;
        }

        text.push_str(segment);
        let display_segment = bidi::visual_reorder(segment);
        let mut spans = vec![
            Span::styled(prefix, theme::accent_bold()),
            Span::raw(display_segment),
        ];
        if i == 0 && !ghost.is_empty() {
            text.push_str(&ghost);
            spans.push(Span::styled(ghost.clone(), theme::dim()));
        }
        lines.push(InputLine {
            line: Line::from(spans),
        });
    }

    (lines, cursor_line, cursor_text)
}

fn input_panel_height(app: &App, width: u16, available_height: u16) -> u16 {
    let content_width = width.max(1) as usize;
    let (lines, _, _) = build_input_lines(app, content_width);
    let visual_rows = lines.len() as u16;
    let max_input = (available_height * 2 / 3).max(5);
    (visual_rows + 1).min(max_input)
}

fn input_cursor_position(app: &App, area: Rect, scroll: u16) -> (u16, u16) {
    let content_width = area.width.max(1) as usize;
    let (_, cursor_line, cursor_text) = build_input_lines(app, content_width);

    let prefix = if cursor_line == 0 { " › " } else { " … " };
    let cursor_display = format!("{}{}", prefix, cursor_text);
    let (cursor_row, cursor_col) = wrapped_position(&cursor_display, content_width);

    let segments: Vec<&str> = if app.input.is_empty() {
        vec![""]
    } else {
        app.input.split('\n').collect()
    };
    let mut row_offset = 0usize;
    for (i, segment) in segments.iter().enumerate() {
        if i >= cursor_line {
            break;
        }
        let p = if i == 0 { " › " } else { " … " };
        let full = format!("{}{}", p, segment);
        row_offset += wrap_line_at_width(&full, content_width).len();
    }

    let absolute_row = (row_offset + cursor_row) as u16;
    (
        area.x + cursor_col as u16,
        area.y + 1 + absolute_row.saturating_sub(scroll),
    )
}

fn render_input(frame: &mut Frame, app: &App, area: Rect) {
    if app.reverse_search_mode {
        let cwd = platform::display_path(&platform::current_dir());
        let title = format!(" {} ", cwd);

        let found = app.find_reverse_match().map(|s| s.to_string());

        let line = Line::from(vec![
            Span::styled("(reverse-i-search) '", theme::dim()),
            Span::styled(app.reverse_search_query.clone(), theme::accent()),
            Span::styled("': ", theme::dim()),
            match &found {
                Some(text) => Span::raw(text.clone()),
                None => Span::styled("no match", theme::dim()),
            },
        ]);

        let input = Paragraph::new(line).block(
            Block::default()
                .borders(Borders::TOP)
                .title(title)
                .title_style(theme::dim())
                .border_style(theme::accent()),
        );
        frame.render_widget(input, area);
        return;
    }

    let content_width = area.width.max(1) as usize;
    let (lines, cursor_line, cursor_text) = build_input_lines(app, content_width);

    let cwd = platform::display_path(&platform::current_dir());
    let title = format!(" {} ", cwd);

    let visible_rows = area.height.saturating_sub(1);

    let prefix = if cursor_line == 0 { " › " } else { " … " };
    let cursor_display = format!("{}{}", prefix, cursor_text);
    let (cursor_wrap_row, _) = wrapped_position(&cursor_display, content_width);

    let segments: Vec<&str> = if app.input.is_empty() {
        vec![""]
    } else {
        app.input.split('\n').collect()
    };
    let mut cursor_absolute_row = 0u16;
    for (i, segment) in segments.iter().enumerate() {
        if i >= cursor_line {
            break;
        }
        let p = if i == 0 { " › " } else { " … " };
        let full = format!("{}{}", p, segment);
        cursor_absolute_row += wrap_line_at_width(&full, content_width).len() as u16;
    }
    cursor_absolute_row += cursor_wrap_row as u16;

    let scroll = if cursor_absolute_row >= visible_rows {
        cursor_absolute_row - visible_rows + 1
    } else {
        0
    };

    let text_lines: Vec<Line> = lines.into_iter().map(|line| line.line).collect();
    let input = Paragraph::new(text_lines).scroll((scroll, 0)).block(
        Block::default()
            .borders(Borders::TOP)
            .title(title)
            .title_style(theme::dim())
            .border_style(theme::accent()),
    );
    frame.render_widget(input, area);

    let (cursor_x, cursor_y) = input_cursor_position(app, area, scroll);
    frame.set_cursor_position((cursor_x, cursor_y));
}

fn render_suggestions(frame: &mut Frame, app: &App, input_area: Rect) {
    let max_visible: usize = 8;

    let (items, title) = if !app.file_suggestions.is_empty() {
        let total = app.file_suggestions.len();
        let visible = total.min(max_visible);
        let scroll_offset = if app.suggestion_cursor >= visible {
            app.suggestion_cursor - visible + 1
        } else {
            0
        };
        let items: Vec<Line> = app
            .file_suggestions
            .iter()
            .enumerate()
            .skip(scroll_offset)
            .take(visible)
            .map(|(i, entry)| {
                let style = if i == app.suggestion_cursor {
                    theme::accent_bold().add_modifier(Modifier::REVERSED)
                } else {
                    Style::default()
                };
                let icon = if entry.is_dir { "d" } else { "f" };
                Line::styled(format!(" {} {}", icon, entry.path), style)
            })
            .collect();
        let title = if total > visible {
            format!(" Files ({}/{}) ", app.suggestion_cursor + 1, total)
        } else {
            " Files ".to_string()
        };
        (items, title)
    } else if !app.arg_suggestions.is_empty() {
        let total = app.arg_suggestions.len();
        let visible = total.min(max_visible);
        let scroll_offset = if app.suggestion_cursor >= visible {
            app.suggestion_cursor - visible + 1
        } else {
            0
        };
        let items: Vec<Line> = app
            .arg_suggestions
            .iter()
            .enumerate()
            .skip(scroll_offset)
            .take(visible)
            .map(|(i, arg)| {
                let style = if i == app.suggestion_cursor {
                    theme::accent_bold().add_modifier(Modifier::REVERSED)
                } else {
                    Style::default()
                };
                Line::styled(format!(" {}", arg), style)
            })
            .collect();
        let title = if total > visible {
            format!(" Options ({}/{}) ", app.suggestion_cursor + 1, total)
        } else {
            " Options ".to_string()
        };
        (items, title)
    } else {
        let total = app.suggestions.len();
        let visible = total.min(max_visible);
        let scroll_offset = if app.suggestion_cursor >= visible {
            app.suggestion_cursor - visible + 1
        } else {
            0
        };
        let items: Vec<Line> = app
            .suggestions
            .iter()
            .enumerate()
            .skip(scroll_offset)
            .take(visible)
            .map(|(i, &cmd_idx)| {
                let cmd = &COMMANDS[cmd_idx];
                let style = if i == app.suggestion_cursor {
                    theme::accent_bold().add_modifier(Modifier::REVERSED)
                } else {
                    Style::default()
                };
                Line::styled(format!(" {:16} {}", cmd.name, cmd.description), style)
            })
            .collect();
        let title = if total > visible {
            format!(" Commands ({}/{}) ", app.suggestion_cursor + 1, total)
        } else {
            " Commands ".to_string()
        };
        (items, title)
    };

    if items.is_empty() {
        return;
    }

    let popup_height = items.len() as u16 + 2;
    let max_item_width = items
        .iter()
        .map(|l| {
            l.spans
                .iter()
                .map(|s| s.content.chars().count())
                .sum::<usize>()
        })
        .max()
        .unwrap_or(30) as u16
        + 4;
    let popup_width = max_item_width.max(30);

    let min_y = 0u16;
    let y = input_area.y.saturating_sub(popup_height);
    let popup_area = if y < min_y {
        Rect {
            x: input_area.x + 3,
            y: input_area.bottom(),
            width: popup_width.min(input_area.width.saturating_sub(4)),
            height: popup_height.min(frame.area().height.saturating_sub(input_area.bottom())),
        }
    } else {
        Rect {
            x: input_area.x + 3,
            y,
            width: popup_width.min(input_area.width.saturating_sub(4)),
            height: popup_height,
        }
    };

    frame.render_widget(Clear, popup_area);

    let popup = Paragraph::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::accent())
            .title(title),
    );
    frame.render_widget(popup, popup_area);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{App, ChatState};

    #[test]
    fn multiline_input_expands_the_panel_height() {
        let mut app = App::new();
        app.active_mut().chat_state = ChatState::Idle;
        app.input = "line1\nline2\nline3".to_string();
        app.input_cursor = app.input.len();

        assert!(input_panel_height(&app, 40, 24) > 3);
    }

    #[test]
    fn wrapped_input_moves_the_cursor_down_a_row() {
        let mut app = App::new();
        app.active_mut().chat_state = ChatState::Idle;
        app.input = "abcdefghijklmnopqrstuvwxyz".to_string();
        app.input_cursor = app.input.len();

        let (cursor_x, cursor_y) = input_cursor_position(
            &app,
            Rect {
                x: 0,
                y: 0,
                width: 16,
                height: 5,
            },
            0,
        );

        assert_eq!(cursor_x, 13);
        assert!(cursor_y > 1);
    }

    #[test]
    fn cursor_aligns_with_charwrap_when_text_has_spaces() {
        let mut app = App::new();
        app.active_mut().chat_state = ChatState::Idle;
        app.input = "hello world this is a longer line".to_string();
        app.input_cursor = app.input.len();

        let (cx, cy) = input_cursor_position(
            &app,
            Rect {
                x: 0,
                y: 0,
                width: 16,
                height: 6,
            },
            0,
        );

        // Content width = 16 (no side borders)
        // Char-wrap of " › hello world this is a longer line" at width 16:
        //   row 0: " › hello world t" (16)
        //   row 1: "his is a longer " (16)
        //   row 2: "line"              (4)
        // cursor_y = 0 + 1 + 2 = 3, cursor_x = 0 + 4 = 4
        assert_eq!(cy, 3);
        assert_eq!(cx, 4);
    }

    #[test]
    fn code_blocks_collected_from_markdown() {
        let text = "Hello\n```rust\nfn main() {}\nlet x = 1;\n```\nBye\n```\nplain block\n```";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);

        assert_eq!(code_blocks.len(), 2);
        assert_eq!(code_blocks[0], "fn main() {}\nlet x = 1;");
        assert_eq!(code_blocks[1], "plain block");
    }

    #[test]
    fn closing_separator_shows_block_index() {
        let text = "```\ncode\n```";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);

        let closing = &lines[2];
        let full_text: String = closing
            .spans
            .iter()
            .map(|s| s.content.to_string())
            .collect();
        assert!(full_text.contains("[1]"));
    }

    #[test]
    fn diff_blocks_not_collected() {
        let text = "```diff\n+added\n-removed\n```";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);

        assert!(code_blocks.is_empty());
    }

    #[test]
    fn rust_code_block_uses_syntax_highlighting() {
        let text = "```rust\nfn main() {}\n```";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);

        let code_line = &lines[1];
        assert!(
            code_line.spans.len() > 1,
            "expected multiple spans from syntax highlighting, got {}",
            code_line.spans.len()
        );
        let prefix: String = code_line.spans[0].content.to_string();
        assert_eq!(prefix, "  │ ");
    }

    #[test]
    fn unknown_lang_code_block_falls_back_to_single_style() {
        let text = "```madeuplang9999\nsome code here\n```";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);

        let code_line = &lines[1];
        assert_eq!(
            code_line.spans.len(),
            1,
            "unknown language should fall back to single-span rendering"
        );
    }

    #[test]
    fn blockquote_renders_with_bar() {
        let (line, _, _) = render_markdown_line("> quoted text", false, false);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("│"));
        assert!(text.contains("quoted text"));
    }

    #[test]
    fn numbered_list_renders_with_number() {
        let (line, _, _) = render_markdown_line("1. first item", false, false);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("1."));
        assert!(text.contains("first item"));
    }

    #[test]
    fn link_renders_text_and_url() {
        let (line, _, _) =
            render_markdown_line("See [docs](https://example.com) here", false, false);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("docs"));
        assert!(text.contains("example.com"));
    }

    #[test]
    fn table_renders_with_aligned_columns() {
        let text = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);
        assert!(lines.len() >= 4);
        let header: String = lines[0]
            .spans
            .iter()
            .map(|s| s.content.to_string())
            .collect();
        assert!(header.contains("Name"));
        assert!(header.contains("Age"));
        let sep: String = lines[1]
            .spans
            .iter()
            .map(|s| s.content.to_string())
            .collect();
        assert!(sep.contains("─"));
    }

    #[test]
    fn table_flush_at_message_end() {
        let text = "| Col1 |\n|---|\n| val |";
        let mut lines = Vec::new();
        let mut code_blocks = Vec::new();
        let mut current_block = None;
        render_markdown_text(text, &mut lines, &mut code_blocks, &mut current_block);
        assert!(!lines.is_empty());
        let all_text: String = lines
            .iter()
            .flat_map(|l| l.spans.iter().map(|s| s.content.to_string()))
            .collect();
        assert!(all_text.contains("val"));
    }

    #[test]
    fn inline_parser_handles_mixed_bold_and_code() {
        let spans = parse_inline_markdown("use **bold** and `code` together");
        let texts: Vec<String> = spans.iter().map(|s| s.content.to_string()).collect();
        assert!(texts.contains(&"bold".to_string()));
        assert!(texts.contains(&"code".to_string()));
        assert!(spans.len() >= 4);
    }

    #[test]
    fn inline_parser_handles_link_with_bold() {
        let spans = parse_inline_markdown("**important** [link](url) text");
        let texts: Vec<String> = spans.iter().map(|s| s.content.to_string()).collect();
        assert!(texts.contains(&"important".to_string()));
        assert!(texts.contains(&"link".to_string()));
    }
}
