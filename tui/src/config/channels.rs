use std::fs;

use super::repo_root;
use crate::platform;

#[derive(Clone)]
pub struct ChannelEntry {
    pub name: String,
    pub configured: bool,
}

fn env_has(key: &str, env_content: &str) -> bool {
    if platform::env_var(key).is_some() {
        return true;
    }
    let prefix = format!("{}=", key);
    env_content
        .lines()
        .any(|line| line.starts_with(&prefix) && line.len() > prefix.len())
}

pub fn load() -> Vec<ChannelEntry> {
    let root = repo_root();
    let env_content = fs::read_to_string(root.join(".env")).unwrap_or_default();
    vec![
        ChannelEntry {
            name: "WhatsApp".to_string(),
            configured: root.join("store").join("auth").join("creds.json").exists(),
        },
        ChannelEntry {
            name: "Telegram".to_string(),
            configured: env_has("TELEGRAM_BOT_TOKEN", &env_content),
        },
        ChannelEntry {
            name: "Discord".to_string(),
            configured: env_has("DISCORD_BOT_TOKEN", &env_content),
        },
        ChannelEntry {
            name: "Slack".to_string(),
            configured: env_has("SLACK_BOT_TOKEN", &env_content),
        },
        ChannelEntry {
            name: "Gmail".to_string(),
            configured: env_has("GMAIL_CLIENT_ID", &env_content),
        },
        ChannelEntry {
            name: "X (Twitter)".to_string(),
            configured: env_has("X_API_KEY", &env_content),
        },
    ]
}
