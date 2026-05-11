use std::process::Command;

pub fn send(title: &str, body: &str) {
    if !cfg!(target_os = "macos") {
        return;
    }
    // strip newlines before escaping — newlines break out of AppleScript strings
    let safe_body = body.replace(['\n', '\r'], " ").replace('\\', "\\\\").replace('"', "\\\"");
    let safe_title = title.replace(['\n', '\r'], " ").replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        "display notification \"{}\" with title \"{}\"",
        safe_body, safe_title
    );
    let _ = Command::new("osascript")
        .args(["-e", &script])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}
