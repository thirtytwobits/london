mod support;

use serde_json::{Value, json};
use std::{fs, path::Path, process::Stdio};
use support::{Peer, Streams, TOKENS, authorisation_server, walkthrough};
use tokio::process::{Child, Command};

fn configuration() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("configuration.json")
}

fn launch(introspection: &str, token: &str, configuration: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_items-mcp"));
    command
        .arg("--configuration")
        .arg(configuration)
        .env("ITEMS_INTROSPECTION_URL", introspection)
        .env("ITEMS_CLIENT_ID", "items-mcp")
        .env("ITEMS_CLIENT_SECRET", "development-only")
        .env("ITEMS_ACCESS_TOKEN", token)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    command
}

type Connection = Peer<Streams<tokio::process::ChildStdout, tokio::process::ChildStdin>>;

async fn connect(mut child: Child) -> (Connection, Child) {
    let transport = Streams::new(child.stdout.take().unwrap(), child.stdin.take().unwrap());
    let peer = Peer::connect(transport).await;
    (peer, child)
}

async fn finish(child: Child) {
    let output = child.wait_with_output().await.unwrap();
    assert!(output.status.success(), "{output:?}");
    assert!(output.stderr.is_empty(), "{output:?}");
}

async fn refused_at_startup(mut command: Command, diagnostic: &str) {
    let output = command.output().await.unwrap();
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert!(output.stdout.is_empty(), "{output:?}");
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.contains(diagnostic), "{stderr}");
}

#[tokio::test]
async fn each_development_token_walks_through_its_grant_and_shuts_down_cleanly() {
    let introspection = authorisation_server(json!({})).await;
    for token in TOKENS {
        let child = launch(&introspection, token, &configuration())
            .spawn()
            .unwrap();
        let (mut peer, child) = connect(child).await;
        walkthrough(token, &mut peer).await;
        drop(peer);
        finish(child).await;
    }
}

#[tokio::test]
async fn a_token_the_authorisation_server_does_not_verify_stops_startup() {
    let (issuer, _) = support::resource();
    let introspection = authorisation_server(json!({
        "elsewhere": {"active":true, "iss":issuer, "aud":["https://elsewhere.example.com"],
                      "sub":"operator", "scope":"items:read items:write items:remove"},
        "revoked": {"active":false},
    }))
    .await;
    for token in ["unknown", "elsewhere", "revoked"] {
        let command = launch(&introspection, token, &configuration());
        refused_at_startup(command, "ITEMS_ACCESS_TOKEN").await;
    }
    let mut command = launch(&introspection, "interactive", &configuration());
    command.env("ITEMS_CLIENT_SECRET", "wrong");
    refused_at_startup(command, "ITEMS_ACCESS_TOKEN").await;
    let mut command = launch(&introspection, "interactive", &configuration());
    command.env_remove("ITEMS_ACCESS_TOKEN");
    refused_at_startup(command, "ITEMS_ACCESS_TOKEN is not set").await;
}

#[tokio::test]
async fn configuration_restrictions_take_effect_on_the_next_launch() {
    let introspection = authorisation_server(json!({})).await;
    let root = tempfile::tempdir().unwrap();
    let edited = root.path().join("configuration with spaces.json");
    let mut json: Value =
        serde_json::from_str(&fs::read_to_string(configuration()).unwrap()).unwrap();
    json["operations"] = json!([
        {"source":"items", "name":"items.list"},
        {"source":"items", "name":"items.add", "require":{"scopes":["items:remove"]}}
    ]);
    fs::write(&edited, serde_json::to_vec_pretty(&json).unwrap()).unwrap();

    let child = launch(&introspection, "assistant", &edited)
        .spawn()
        .unwrap();
    let (mut peer, child) = connect(child).await;
    assert_eq!(peer.names().await, ["items.list"]);
    let denied = peer
        .call("items.add", json!({"id":"c", "text":"Add an item"}))
        .await;
    support::refusal(&denied, "insufficient_scope", "refused", "not_dispatched");
    drop(peer);
    finish(child).await;

    let child = launch(&introspection, "interactive", &edited)
        .spawn()
        .unwrap();
    let (mut peer, child) = connect(child).await;
    assert_eq!(peer.names().await, ["items.add", "items.list"]);
    drop(peer);
    finish(child).await;
}

#[tokio::test]
async fn an_unusable_configuration_stops_startup_with_a_diagnostic() {
    let introspection = authorisation_server(json!({})).await;
    let root = tempfile::tempdir().unwrap();
    let file = root.path().join("configuration.json");
    for contents in [
        "{",
        r#"{"oauth":{"issuer":"https://identity.example.com","audience":"https://items.example.com"},"sources":{"items":{"provider":"items"}},"operations":[{"source":"items","name":"items.absent"}]}"#,
        r#"{"sources":{"items":{"provider":"items"}},"operations":[{"source":"items","name":"items.list"}]}"#,
    ] {
        fs::write(&file, contents).unwrap();
        refused_at_startup(
            launch(&introspection, "interactive", &file),
            "configuration.json",
        )
        .await;
    }
    let absent = root.path().join("absent.json");
    refused_at_startup(
        launch(&introspection, "interactive", &absent),
        "absent.json",
    )
    .await;
}

#[test]
fn launch_configuration_is_explicit() {
    for args in [
        vec![],
        vec!["--configuration"],
        vec!["--configuration", "configuration.json", "extra"],
        vec!["--configuration", "configuration.json", "--http"],
        vec![
            "--http",
            "127.0.0.1:0",
            "--configuration",
            "configuration.json",
        ],
        vec!["--principal", "human"],
    ] {
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_items-mcp"))
            .args(args)
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_items-mcp"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(output.status.success());
    let help = String::from_utf8(output.stdout).unwrap();
    for name in [
        "--configuration",
        "--http",
        "ITEMS_ACCESS_TOKEN",
        "ITEMS_INTROSPECTION_URL",
        "ITEMS_CLIENT_ID",
        "ITEMS_CLIENT_SECRET",
    ] {
        assert!(help.contains(name), "{name}");
    }
}
