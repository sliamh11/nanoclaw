use std::sync::OnceLock;

use ratatui::prelude::*;
use syntect::easy::HighlightLines;
use syntect::highlighting::{Theme, ThemeSet};
use syntect::parsing::SyntaxSet;

static SYNTAX_SET: OnceLock<SyntaxSet> = OnceLock::new();
static THEME: OnceLock<Theme> = OnceLock::new();

fn ss() -> &'static SyntaxSet {
    SYNTAX_SET.get_or_init(SyntaxSet::load_defaults_newlines)
}

fn theme() -> &'static Theme {
    THEME.get_or_init(|| {
        let ts = ThemeSet::load_defaults();
        ts.themes["base16-ocean.dark"].clone()
    })
}

pub fn highlight_line(text: &str, lang: &str) -> Option<Vec<Span<'static>>> {
    let syntax = ss().find_syntax_by_token(lang)?;
    let mut h = HighlightLines::new(syntax, theme());
    let regions = h.highlight_line(text, ss()).ok()?;
    let spans = regions
        .into_iter()
        .map(|(style, text)| {
            let fg = Color::Rgb(style.foreground.r, style.foreground.g, style.foreground.b);
            Span::styled(text.to_string(), Style::default().fg(fg))
        })
        .collect();
    Some(spans)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rust_fn_produces_multiple_spans() {
        let spans = highlight_line("fn main() {}", "rust");
        assert!(spans.is_some());
        let spans = spans.unwrap();
        assert!(
            spans.len() > 1,
            "expected multiple spans, got {}",
            spans.len()
        );
    }

    #[test]
    fn unknown_lang_returns_none() {
        let result = highlight_line("some text", "madeuplanguage9999");
        assert!(result.is_none());
    }

    #[test]
    fn python_keyword_highlighted() {
        let spans = highlight_line("def hello():", "python");
        assert!(spans.is_some());
        let spans = spans.unwrap();
        assert!(spans.len() > 1);
    }
}
