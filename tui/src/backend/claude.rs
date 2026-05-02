use super::{Backend, ChunkKind, ModelDef, RunConfig, SUBAGENT_TOOLS_CLAUDE, StreamChunk};
use std::process::{Command, Stdio};

pub struct ClaudeBackend;

static MODELS: &[ModelDef] = &[
    ModelDef {
        id: "opus-4-7",
        display: "Opus 4.7",
        context: "200K",
    },
    ModelDef {
        id: "opus",
        display: "Opus 4.6",
        context: "1M",
    },
    ModelDef {
        id: "opus-200k",
        display: "Opus 4.6",
        context: "200K",
    },
    ModelDef {
        id: "sonnet",
        display: "Sonnet 4.6",
        context: "200K",
    },
    ModelDef {
        id: "haiku",
        display: "Haiku 4.5",
        context: "200K",
    },
];

impl Backend for ClaudeBackend {
    fn name(&self) -> &'static str {
        "claude"
    }
    fn display_name(&self) -> &'static str {
        "Anthropic (Claude)"
    }
    fn models(&self) -> &'static [ModelDef] {
        MODELS
    }

    fn build_command(&self, config: &RunConfig) -> Command {
        let mut cmd = Command::new("claude");
        cmd.args([
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            &config.model,
            "--effort",
            &config.effort,
        ]);
        if config.bypass_permissions {
            cmd.arg("--dangerously-skip-permissions");
        }
        if config.is_continuation {
            cmd.arg("--continue");
        }
        if !config.is_continuation
            && let Some(ref ctx_file) = config.system_context_file
            && let Ok(ctx) = std::fs::read_to_string(ctx_file)
            && !ctx.is_empty()
        {
            cmd.args(["--append-system-prompt", &ctx]);
        }
        cmd.arg(&config.message);
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        cmd
    }

    fn parse_line(&self, line: &str) -> Vec<StreamChunk> {
        let v: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        };
        let event_type = match v.get("type").and_then(|t| t.as_str()) {
            Some(t) => t,
            None => return Vec::new(),
        };

        match event_type {
            "assistant" => {
                let content = match v
                    .get("message")
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_array())
                {
                    Some(c) => c,
                    None => return Vec::new(),
                };
                let mut chunks = Vec::new();
                for block in content {
                    let block_type = match block.get("type").and_then(|t| t.as_str()) {
                        Some(t) => t,
                        None => continue,
                    };
                    match block_type {
                        "text" => {
                            if let Some(text) = block.get("text").and_then(|t| t.as_str()) {
                                chunks.push(StreamChunk {
                                    kind: ChunkKind::Text(text.to_string()),
                                });
                            }
                        }
                        "thinking" => {
                            if let Some(text) = block.get("thinking").and_then(|t| t.as_str()) {
                                chunks.push(StreamChunk {
                                    kind: ChunkKind::Thinking(text.to_string()),
                                });
                            }
                        }
                        "tool_use" => {
                            let id = block
                                .get("id")
                                .and_then(|i| i.as_str())
                                .unwrap_or("")
                                .to_string();
                            let name = block
                                .get("name")
                                .and_then(|n| n.as_str())
                                .unwrap_or("unknown");
                            let input = block.get("input");

                            if SUBAGENT_TOOLS_CLAUDE.contains(&name) {
                                let subagent_type = input
                                    .and_then(|i| i.get("subagent_type"))
                                    .and_then(|s| s.as_str())
                                    .unwrap_or(name)
                                    .to_string();
                                let description = input
                                    .and_then(|i| i.get("description"))
                                    .and_then(|d| d.as_str())
                                    .unwrap_or("")
                                    .to_string();
                                chunks.push(StreamChunk {
                                    kind: ChunkKind::SubagentStart {
                                        id,
                                        subagent_type,
                                        description,
                                    },
                                });
                            } else {
                                let detail = input
                                    .map(|i| {
                                        if let Some(cmd) = i.get("command").and_then(|c| c.as_str())
                                        {
                                            cmd.chars().take(80).collect::<String>()
                                        } else if let Some(path) =
                                            i.get("file_path").and_then(|p| p.as_str())
                                        {
                                            path.to_string()
                                        } else if let Some(desc) =
                                            i.get("description").and_then(|d| d.as_str())
                                        {
                                            desc.chars().take(80).collect::<String>()
                                        } else {
                                            String::new()
                                        }
                                    })
                                    .unwrap_or_default();
                                chunks.push(StreamChunk {
                                    kind: ChunkKind::ToolUse {
                                        id,
                                        tool: name.to_string(),
                                        detail,
                                    },
                                });
                            }
                        }
                        _ => {}
                    }
                }
                chunks
            }
            "user" => {
                let content = match v
                    .get("message")
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_array())
                {
                    Some(c) => c,
                    None => return Vec::new(),
                };
                let mut chunks = Vec::new();
                for block in content {
                    if block.get("type").and_then(|t| t.as_str()) == Some("tool_result") {
                        let id = block
                            .get("tool_use_id")
                            .and_then(|i| i.as_str())
                            .unwrap_or("")
                            .to_string();
                        let preview = extract_tool_result_preview(block);
                        chunks.push(StreamChunk {
                            kind: ChunkKind::ToolResult {
                                id,
                                content_preview: preview,
                            },
                        });
                    }
                }
                chunks
            }
            "result" => {
                let cost = v
                    .get("total_cost_usd")
                    .and_then(|c| c.as_f64())
                    .unwrap_or(0.0);
                let usage = v.get("usage");
                let input = usage
                    .and_then(|u| u.get("input_tokens"))
                    .and_then(|t| t.as_u64())
                    .unwrap_or(0);
                let output = usage
                    .and_then(|u| u.get("output_tokens"))
                    .and_then(|t| t.as_u64())
                    .unwrap_or(0);
                vec![StreamChunk {
                    kind: ChunkKind::CostUpdate {
                        cost_usd: cost,
                        input_tokens: input,
                        output_tokens: output,
                    },
                }]
            }
            _ => Vec::new(),
        }
    }
}

fn extract_tool_result_preview(block: &serde_json::Value) -> String {
    if let Some(content) = block.get("content") {
        if let Some(s) = content.as_str() {
            return s.chars().take(200).collect();
        }
        if let Some(arr) = content.as_array() {
            for item in arr {
                if let Some(text) = item.get("text").and_then(|t| t.as_str()) {
                    return text.chars().take(200).collect();
                }
            }
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(json: &str) -> Vec<ChunkKind> {
        ClaudeBackend
            .parse_line(json)
            .into_iter()
            .map(|c| c.kind)
            .collect()
    }

    #[test]
    fn parse_text_event() {
        let json = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(matches!(&chunks[0], ChunkKind::Text(t) if t == "hello"));
    }

    #[test]
    fn parse_thinking_event() {
        let json = r#"{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"reasoning..."}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(matches!(&chunks[0], ChunkKind::Thinking(t) if t == "reasoning..."));
    }

    #[test]
    fn parse_tool_use_event() {
        let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(matches!(&chunks[0], ChunkKind::ToolUse { tool, .. } if tool == "Bash"));
    }

    #[test]
    fn parse_subagent_event() {
        let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"a1","name":"Agent","input":{"subagent_type":"Explore","description":"find files"}}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(
            matches!(&chunks[0], ChunkKind::SubagentStart { subagent_type, .. } if subagent_type == "Explore")
        );
    }

    #[test]
    fn parse_multi_block_event() {
        let json = r#"{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"hmm"},{"type":"text","text":"answer"}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 2);
        assert!(matches!(&chunks[0], ChunkKind::Thinking(_)));
        assert!(matches!(&chunks[1], ChunkKind::Text(_)));
    }

    #[test]
    fn parse_tool_result_with_id() {
        let json = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"output"}]}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(
            matches!(&chunks[0], ChunkKind::ToolResult { id, content_preview } if id == "t1" && content_preview == "output")
        );
    }

    #[test]
    fn parse_cost_update() {
        let json = r#"{"type":"result","total_cost_usd":0.05,"usage":{"input_tokens":100,"output_tokens":200}}"#;
        let chunks = parse(json);
        assert_eq!(chunks.len(), 1);
        assert!(matches!(
            &chunks[0],
            ChunkKind::CostUpdate {
                input_tokens: 100,
                output_tokens: 200,
                ..
            }
        ));
    }

    #[test]
    fn parse_invalid_json() {
        let chunks = parse("not json");
        assert!(chunks.is_empty());
    }
}
