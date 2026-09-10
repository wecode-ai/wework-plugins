// SPDX-License-Identifier: MIT
//! Private native credential channel and in-memory upstream integration.
use crate::auth::Bot;
use crate::mcp::config::{McpBindSource, McpConfigItem};
use anyhow::{Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

static BOT: OnceLock<Bot> = OnceLock::new();
static CONFIG: Mutex<Option<Vec<McpConfigItem>>> = Mutex::new(None);
static OUTPUT: Mutex<(String, bool)> = Mutex::new((String::new(), false));
const MAX_OUTPUT: usize = 8 * 1024 * 1024;

pub fn managed() -> bool {
    BOT.get().is_some()
}
pub fn bot() -> Option<Bot> {
    BOT.get().cloned()
}
pub fn config() -> Option<Vec<McpConfigItem>> {
    CONFIG.lock().ok()?.clone()
}
pub fn save_config(items: &[McpConfigItem]) -> Result<()> {
    for item in items {
        if let Some(value) = &item.url {
            let url = reqwest::Url::parse(value)?;
            ensure!(
                url.scheme() == "https"
                    && url.username().is_empty()
                    && url.password().is_none()
                    && url.port_or_known_default() == Some(443),
                "invalid capability origin"
            );
        }
    }
    *CONFIG
        .lock()
        .map_err(|_| anyhow::anyhow!("state unavailable"))? = Some(items.to_vec());
    Ok(())
}
pub fn emit(value: std::fmt::Arguments<'_>) {
    if !managed() {
        std::println!("{value}");
        return;
    }
    if let Ok(mut output) = OUTPUT.lock() {
        let value = value.to_string();
        if output.0.len() + value.len() + 1 > MAX_OUTPUT {
            output.1 = true;
        } else {
            output.0.push_str(&value);
            output.0.push('\n');
        }
    }
}
fn flush() -> Result<()> {
    let output = OUTPUT
        .lock()
        .map_err(|_| anyhow::anyhow!("output unavailable"))?;
    ensure!(!output.1, "output exceeded limit");
    if let Some(bot) = BOT.get() {
        ensure!(!output.0.contains(&bot.secret), "private output");
    }
    if let Some(items) = config() {
        for item in items {
            if let Some(url) = item.url {
                ensure!(!output.0.contains(&url), "private output");
            }
        }
    }
    std::io::stdout().write_all(output.0.as_bytes())?;
    Ok(())
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Credential {
    username: String,
    password: String,
}
impl Credential {
    fn validate(&self) -> Result<()> {
        for value in [&self.username, &self.password] {
            ensure!(
                !value.trim().is_empty()
                    && value.len() <= 4096
                    && !value.chars().any(char::is_control),
                "invalid credential"
            );
        }
        ensure!(self.username.len() <= 256, "invalid account");
        Ok(())
    }
    fn to_bot(&self) -> Bot {
        Bot {
            id: self.username.clone(),
            secret: self.password.clone(),
            create_time: 0,
        }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Frame {
    protocol_version: u8,
    connector_slug: String,
    credential_type: String,
    credential: Credential,
}
fn connect() -> Result<TcpStream> {
    ensure!(
        std::env::var_os("WEGENT_PLUGIN_AUTH_FD").is_none(),
        "invalid transport"
    );
    let port: u16 = std::env::var("WEGENT_PLUGIN_AUTH_PORT")?.parse()?;
    ensure!(port != 0, "invalid transport");
    let mut nonce = [0u8; 32];
    std::io::stdin().read_exact(&mut nonce)?;
    let mut stream = TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_secs(5),
    )?;
    stream.set_read_timeout(Some(Duration::from_secs(300)))?;
    stream.set_write_timeout(Some(Duration::from_secs(300)))?;
    stream.write_all(&nonce)?;
    Ok(stream)
}
fn receive(stream: &mut impl Read) -> Result<Credential> {
    let mut size = [0; 4];
    stream.read_exact(&mut size)?;
    let size = u32::from_be_bytes(size) as usize;
    ensure!(size > 0 && size <= 65536, "invalid frame");
    let mut payload = vec![0; size];
    stream.read_exact(&mut payload)?;
    let frame: Frame = serde_json::from_slice(&payload)?;
    ensure!(
        frame.protocol_version == 1
            && frame.connector_slug == "wecom"
            && frame.credential_type == "password",
        "invalid frame"
    );
    frame.credential.validate()?;
    Ok(frame.credential)
}
fn send(stream: &mut impl Write, credential: Credential) -> Result<()> {
    credential.validate()?;
    let frame = Frame {
        protocol_version: 1,
        connector_slug: "wecom".into(),
        credential_type: "password".into(),
        credential,
    };
    let data = serde_json::to_vec(&frame)?;
    ensure!(data.len() <= 65536, "invalid frame");
    stream.write_all(&(data.len() as u32).to_be_bytes())?;
    stream.write_all(&data)?;
    Ok(())
}
pub fn allowed(args: &[String]) -> bool {
    args.first().is_some_and(|name| {
        matches!(
            name.as_str(),
            "contact" | "doc" | "meeting" | "msg" | "schedule" | "todo" | "account-status"
        )
    })
}
async fn verify(bot: Bot) -> Result<()> {
    BOT.set(bot)
        .map_err(|_| anyhow::anyhow!("credential already set"))?;
    crate::mcp::config::fetch_mcp_config(McpBindSource::Interactive).await?;
    Ok(())
}
pub async fn execute(args: Vec<String>) -> Result<()> {
    match args.first().map(String::as_str) {
        Some("export") if args.len() == 1 => {
            let mut stream = connect()?;
            let value =
                crate::auth::get_bot_info().ok_or_else(|| anyhow::anyhow!("login required"))?;
            let credential = Credential {
                username: value.id.clone(),
                password: value.secret.clone(),
            };
            credential.validate()?;
            verify(value).await?;
            let account = credential.username.clone();
            send(&mut stream, credential)?;
            std::println!(
                "{}",
                serde_json::json!({"status":"ok","protocolVersion":1,"credentialType":"password","accountId":account})
            );
        }
        Some("run") if allowed(&args[1..]) => {
            let mut stream = connect()?;
            let credential = receive(&mut stream)?;
            drop(stream);
            verify(credential.to_bot()).await?;
            if args[1..] == ["account-status"] {
                emit(format_args!(
                    "{}",
                    serde_json::json!({"status":"ok","accountId":credential.username})
                ));
            } else {
                crate::run_cli(args[1..].to_vec()).await?;
            }
            flush()?;
        }
        Some("local-health") if args.len() == 1 => {
            let value =
                crate::auth::get_bot_info().ok_or_else(|| anyhow::anyhow!("login required"))?;
            verify(value).await?;
        }
        Some("local-clear") if args.len() == 1 => {
            for name in ["bot.enc", "mcp_config.enc"] {
                match std::fs::remove_file(crate::paths::wecom_home_dir().join(name)) {
                    Ok(()) => (),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                    Err(e) => return Err(e.into()),
                }
            }
        }
        _ => bail!("unsupported operation"),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn frame(value: &str) -> Vec<u8> {
        let mut data = (value.len() as u32).to_be_bytes().to_vec();
        data.extend(value.as_bytes());
        data
    }
    #[test]
    fn private_channel_roundtrip_and_duplicate_rejection() {
        let mut data = Vec::new();
        send(
            &mut data,
            Credential {
                username: "synthetic-bot".into(),
                password: "synthetic-secret".into(),
            },
        )
        .unwrap();
        assert_eq!(
            receive(&mut data.as_slice()).unwrap().username,
            "synthetic-bot"
        );
        for value in [
            r#"{"protocolVersion":1,"connectorSlug":"wecom","credentialType":"password","credential":{"username":"x","password":"a","password":"b"}}"#,
            r#"{"protocolVersion":1,"connectorSlug":"other","credentialType":"password","credential":{"username":"x","password":"a"}}"#,
            r#"{"protocolVersion":1,"connectorSlug":"wecom","credentialType":"password","credential":{"username":"x","password":"a","token":"b"}}"#,
        ] {
            assert!(receive(&mut frame(value).as_slice()).is_err());
        }
        assert!(receive(&mut &[0u8, 1, 0, 1][..]).is_err());
        assert!(receive(&mut &[0u8, 0, 0, 2, 123][..]).is_err());
    }
    #[tokio::test]
    async fn managed_source_and_capabilities_stay_in_memory() {
        use base64::Engine;
        let source = tempfile::tempdir().unwrap();
        unsafe {
            std::env::set_var("WECOM_CLI_CONFIG_DIR", source.path());
        }
        let key = [7u8; 32];
        let original = Bot {
            id: "synthetic-bot".into(),
            secret: "synthetic-secret".into(),
            create_time: 42,
        };
        let encrypted = crate::crypto::encrypt_data(&original, &key).unwrap();
        std::fs::write(source.path().join("bot.enc"), &encrypted).unwrap();
        std::fs::write(
            source.path().join(".encryption_key"),
            base64::prelude::BASE64_STANDARD.encode(key),
        )
        .unwrap();
        assert_eq!(
            crate::auth::get_bot_info().unwrap().secret,
            "synthetic-secret"
        );
        assert_eq!(
            std::fs::read(source.path().join("bot.enc")).unwrap(),
            encrypted
        );
        BOT.set(Bot {
            id: "synthetic-bot".into(),
            secret: "synthetic-secret".into(),
            create_time: 0,
        })
        .unwrap();
        assert_eq!(crate::auth::get_bot_info().unwrap().id, "synthetic-bot");
        assert!(crate::auth::set_bot_info(&bot().unwrap()).is_err());
        assert!(crate::crypto::load_existing_key().is_none());
        let items = vec![McpConfigItem {
            url: Some("https://work.weixin.qq.com/synthetic-capability".into()),
            transport_type: Some("streamable-http".into()),
            is_authed: Some(true),
            biz_type: Some("msg".into()),
        }];
        crate::mcp::config::save_mcp_config(&items).unwrap();
        assert_eq!(crate::mcp::config::load_mcp_config().unwrap().len(), 1);
        assert!(allowed(&["doc".into(), "+create".into()]));
        assert!(!allowed(&["init".into()]));
        // Drive the unchanged upstream parser and JSON-RPC client against a
        // synthetic loopback endpoint. Production capability validation requires HTTPS.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        CONFIG.lock().unwrap().as_mut().unwrap()[0].url =
            Some(format!("http://{address}/synthetic"));
        let worker = std::thread::spawn(move || {
            for expected in ["tools/list", "tools/call"] {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(10)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut chunk = [0; 2048];
                loop {
                    let count = socket.read(&mut chunk).unwrap();
                    assert!(count > 0);
                    bytes.extend_from_slice(&chunk[..count]);
                    if let Some(index) = bytes.windows(4).position(|value| value == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..index]);
                        let length: usize = headers
                            .lines()
                            .find_map(|line| {
                                line.to_lowercase()
                                    .strip_prefix("content-length: ")
                                    .map(str::to_string)
                            })
                            .unwrap()
                            .parse()
                            .unwrap();
                        if bytes.len() >= index + 4 + length {
                            break;
                        }
                    }
                }
                let request = String::from_utf8(bytes).unwrap();
                assert!(request.contains(expected));
                assert!(!request.contains("synthetic-secret"));
                let body = if expected == "tools/list" {
                    r#"{"jsonrpc":"2.0","result":{"tools":[{"name":"synthetic_get","inputSchema":{"type":"object"}}]}}"#
                } else {
                    r#"{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"synthetic-business-result"}]}}"#
                };
                write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
            }
        });
        crate::run_cli(vec!["msg".into(), "synthetic_get".into(), "{}".into()])
            .await
            .unwrap();
        worker.join().unwrap();
        assert!(
            OUTPUT
                .lock()
                .unwrap()
                .0
                .contains("synthetic-business-result")
        );
        emit(format_args!("synthetic-secret"));
        assert!(flush().is_err());
    }
}
