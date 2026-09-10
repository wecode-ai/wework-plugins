"""Build the pinned original WeCom CLI with an in-memory auth boundary."""

import argparse
import hashlib
import json
import lzma
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REVISION = "72e14f7695f34d28f1ff23ea504ddd2210a87c13"
SOURCE_SHA256 = "b8af1eeffd346646f1a1a20dbe11cb4ab50d12c26664ddfd433661402fe8840e"
RUST_VERSION = "1.94.1"
TARGETS = {
    "darwin-arm64": "aarch64-apple-darwin",
    "darwin-amd64": "x86_64-apple-darwin",
    "linux-amd64": "x86_64-unknown-linux-gnu",
    "linux-arm64": "aarch64-unknown-linux-gnu",
    "windows-amd64": "x86_64-pc-windows-msvc",
}


def source_hash():
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [ROOT / "build.py", ROOT / "wegent.rs"]
    }


def replace(path, old, new):
    value = path.read_text(encoding="utf-8")
    if value.count(old) != 1:
        raise ValueError(f"Upstream boundary changed: {path.name}")
    path.write_text(value.replace(old, new), encoding="utf-8")


def prepare(directory, archive=None):
    archive = archive or directory / "source.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(
            f"https://codeload.github.com/WecomTeam/wecom-cli/tar.gz/{REVISION}",
            timeout=60,
        ) as response:
            archive.write_bytes(response.read(100 * 1024 * 1024 + 1))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Upstream source checksum mismatch")
    with tarfile.open(archive) as source:
        source.extractall(directory, filter="data")
    source = directory / ("wecom-cli-" + REVISION)
    src = source / "src"
    shutil.copyfile(ROOT / "wegent.rs", src / "wegent.rs")
    path = src / "main.rs"
    value = path.read_text(encoding="utf-8")
    start = value.index("/// Entry point:")
    body = value[start:]
    body = body.replace(
        "/// Entry point: parse CLI arguments and dispatch to the corresponding subcommand handler.\n#[tokio::main]\nasync fn main() -> Result<()> {\n    dotenvy::dotenv().ok();\n\n    logging::init_logging();",
        "async fn run_cli(arguments: Vec<String>) -> Result<()> {",
    )
    body = body.replace(
        "std::env::args()\n        .skip(1)", "arguments.iter().cloned()"
    )
    body = body.replace(
        "let matches = cmd.get_matches();",
        'let matches = match cmd.try_get_matches_from(std::iter::once("wecom-cli".to_string()).chain(arguments)) { Ok(value) => value, Err(error) if matches!(error.kind(), clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion) => { println!("{}", error); return Ok(()); }, Err(error) => return Err(error.into()) };',
    )
    main = """\n#[tokio::main]\nasync fn main() {\n    let arguments: Vec<String> = std::env::args().skip(1).collect();\n    let result = if arguments.first().map(String::as_str) == Some("__wegent") {\n        wegent::execute(arguments[1..].to_vec()).await\n    } else {\n        // The launcher owns the environment; never load workspace .env files.\n        run_cli(arguments).await\n    };\n    if result.is_err() { std::eprintln!("plugin_auth_wecom_failed"); std::process::exit(1); }\n}\n"""
    path.write_text(
        'macro_rules! println { () => { crate::wegent::emit(format_args!("")) }; ($($arg:tt)*) => { crate::wegent::emit(format_args!($($arg)*)) }; }\nmod wegent;\n'
        + value[:start]
        + body
        + main,
        encoding="utf-8",
    )
    replace(
        src / "auth/bot.rs",
        "pub fn get_bot_info() -> Option<Bot> {",
        "pub fn get_bot_info() -> Option<Bot> {\n    if crate::wegent::managed() { return crate::wegent::bot(); }",
    )
    replace(
        src / "auth/bot.rs",
        "pub fn set_bot_info(bot: &Bot) -> Result<()> {",
        'pub fn set_bot_info(bot: &Bot) -> Result<()> {\n    anyhow::ensure!(!crate::wegent::managed(), "managed source denied");',
    )
    replace(
        src / "auth/bot.rs",
        "pub fn clear_bot_info() {",
        "pub fn clear_bot_info() {\n    if crate::wegent::managed() { return; }",
    )
    replace(
        src / "mcp/config.rs",
        "pub fn load_mcp_config() -> Option<Vec<McpConfigItem>> {",
        "pub fn load_mcp_config() -> Option<Vec<McpConfigItem>> {\n    if crate::wegent::managed() { return crate::wegent::config(); }",
    )
    replace(
        src / "mcp/config.rs",
        "pub fn save_mcp_config(items: &[McpConfigItem]) -> Result<()> {",
        "pub fn save_mcp_config(items: &[McpConfigItem]) -> Result<()> {\n    if crate::wegent::managed() { return crate::wegent::save_config(items); }",
    )
    replace(
        src / "mcp/config.rs",
        "pub fn clear_mcp_config() {",
        "pub fn clear_mcp_config() {\n    if crate::wegent::managed() { return; }",
    )
    replace(
        src / "crypto/keystore.rs",
        "pub fn load_existing_key() -> Option<[u8; 32]> {",
        "pub fn load_existing_key() -> Option<[u8; 32]> {\n    if crate::wegent::managed() { return None; }",
    )
    replace(
        src / "crypto/keystore.rs",
        "pub fn save_key(key: &[u8; 32]) -> Result<()> {",
        'pub fn save_key(key: &[u8; 32]) -> Result<()> {\n    anyhow::ensure!(!crate::wegent::managed(), "managed source denied");',
    )
    replace(
        src / "crypto/keystore.rs",
        "pub fn try_decrypt_data<T: serde::de::DeserializeOwned>(data: &[u8]) -> Result<T> {",
        'pub fn try_decrypt_data<T: serde::de::DeserializeOwned>(data: &[u8]) -> Result<T> {\n    anyhow::ensure!(!crate::wegent::managed(), "managed source denied");',
    )
    replace(
        src / "registry.rs",
        "if let Some(tools) = get_cache_content::<Vec<ServiceTool>>(&cache_file) {",
        "if !crate::wegent::managed() && let Some(tools) = get_cache_content::<Vec<ServiceTool>>(&cache_file) {",
    )
    replace(
        src / "registry.rs",
        "if let Ok(json) = serde_json::to_string(&tools) {",
        "if !crate::wegent::managed() && let Ok(json) = serde_json::to_string(&tools) {",
    )
    for name in ["mcp/config.rs", "json_rpc.rs"]:
        replace(
            src / name,
            "reqwest::Client::builder()\n        .build()",
            "reqwest::Client::builder()\n        .no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(std::time::Duration::from_secs(30))\n        .build()",
        )
    return source


def build(output, archive=None):
    expected_sources = source_hash()
    version = subprocess.check_output(["rustc", "--version"], text=True).split()[1]
    if version != RUST_VERSION:
        raise ValueError("Use Rust " + RUST_VERSION)
    host = (
        subprocess.check_output(["rustc", "-vV"], text=True)
        .split("host: ")[1]
        .splitlines()[0]
    )
    target = next((key for key, value in TARGETS.items() if value == host), None)
    if target is None:
        raise ValueError("Unsupported build host")
    with tempfile.TemporaryDirectory(prefix="wecom-build-") as temporary:
        root = prepare(Path(temporary), archive)
        env = {
            **os.environ,
            "CARGO_TARGET_DIR": str(output.parent / "wecom-cargo-target"),
            "CARGO_PROFILE_RELEASE_STRIP": "true",
            "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "1",
            "CARGO_PROFILE_RELEASE_LTO": "thin",
        }
        subprocess.run(
            ["cargo", "test", "--locked", "wegent::tests", "--", "--test-threads=1"],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            ["cargo", "build", "--locked", "--release"], cwd=root, env=env, check=True
        )
        binary = (
            Path(env["CARGO_TARGET_DIR"])
            / "release"
            / ("wecom-cli.exe" if os.name == "nt" else "wecom-cli")
        )
        content = binary.read_bytes()
        folder = output / target
        folder.mkdir(parents=True, exist_ok=True)
        blob = folder / "wecom-account-auth.xz"
        blob.write_bytes(lzma.compress(content, preset=9))
        if source_hash() != expected_sources:
            raise ValueError("Build sources changed during compilation")
        metadata = {
            "nativeProtocolVersion": 1,
            "target": target.replace("-", "/", 1),
            "upstreamVersion": "0.1.9",
            "upstreamRevision": REVISION,
            "upstreamSourceSha256": SOURCE_SHA256,
            "rustVersion": RUST_VERSION,
            "sources": expected_sources,
            "binaryBytes": len(content),
            "binarySha256": hashlib.sha256(content).hexdigest(),
            "compressedSha256": hashlib.sha256(blob.read_bytes()).hexdigest(),
        }
        blob.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
        shutil.copyfile(root / "LICENSE", folder / "UPSTREAM-LICENSE")
        print(json.dumps(metadata))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    build(args.output.resolve(), args.source_archive)
