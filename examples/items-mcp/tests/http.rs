mod support;

use serde_json::json;
use std::{path::Path, process::Stdio};
use support::{Http, Peer, Reply, TOKENS, authorisation_server, lacks_remove_scope, walkthrough};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};

const CHALLENGE: &str =
    r#"Bearer resource_metadata="https://items.example.com/.well-known/oauth-protected-resource""#;

fn launch(introspection: &str, secret: &str, address: &str) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_items-mcp"));
    command
        .arg("--configuration")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("configuration.json"))
        .args(["--http", address])
        .env("ITEMS_INTROSPECTION_URL", introspection)
        .env("ITEMS_CLIENT_ID", "items-mcp")
        .env("ITEMS_CLIENT_SECRET", secret)
        .env_remove("ITEMS_ACCESS_TOKEN")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    command
}

/// Start a server on a loopback port the system chooses, returning it and its MCP endpoint.
async fn serve(introspection: &str, secret: &str) -> (Child, String) {
    let mut child = launch(introspection, secret, "127.0.0.1:0")
        .spawn()
        .unwrap();
    let mut endpoint = String::new();
    BufReader::new(child.stdout.as_mut().unwrap())
        .read_line(&mut endpoint)
        .await
        .unwrap();
    let endpoint = endpoint.trim_end().to_owned();
    assert!(endpoint.starts_with("http://127.0.0.1:"), "{endpoint}");
    assert!(endpoint.ends_with("/mcp"), "{endpoint}");
    (child, endpoint)
}

async fn connect(endpoint: &str, token: &str) -> Peer<Http> {
    Peer::connect(Http::new(endpoint, Some(token))).await
}

fn not_authenticated(reply: Reply) {
    let Reply::Http {
        status,
        challenge,
        body,
    } = reply
    else {
        panic!("expected an HTTP refusal: {reply:?}");
    };
    assert_eq!(status, 401, "{body}");
    assert_eq!(challenge.as_deref(), Some(CHALLENGE));
    assert_eq!(body["code"], "not_authenticated", "{body}");
    assert_eq!(body["evidence"], "not_dispatched", "{body}");
}

#[tokio::test]
async fn each_development_token_walks_through_its_grant() {
    let introspection = authorisation_server(json!({})).await;
    for token in TOKENS {
        let (_server, endpoint) = serve(&introspection, "development-only").await;
        let mut peer = connect(&endpoint, token).await;
        walkthrough(token, &mut peer).await;
    }
}

#[tokio::test]
async fn one_server_verifies_each_request_under_its_own_token() {
    let introspection = authorisation_server(json!({})).await;
    let (_server, endpoint) = serve(&introspection, "development-only").await;
    let mut assistant = connect(&endpoint, "assistant").await;
    let mut interactive = connect(&endpoint, "interactive").await;
    let mut colleague = connect(&endpoint, "colleague").await;

    let item = json!({"id":"c", "text":"Add an item", "owner":"operator"});
    let added = assistant
        .succeeds("items.add", json!({"id":"c", "text":"Add an item"}))
        .await;
    assert_eq!(added, item);
    assert_eq!(colleague.items().await["items"][2], item);

    let denied = assistant
        .reply(
            "tools/call",
            json!({"name":"items.remove", "arguments":{"id":"c"}}),
        )
        .await;
    assert!(matches!(denied, Reply::Http { .. }), "{denied:?}");
    lacks_remove_scope(&denied);
    let denied = colleague.call("items.remove", json!({"id":"c"})).await;
    support::refusal(&denied, "not_owner", "refused", "not_dispatched");
    assert_eq!(
        interactive
            .succeeds("items.remove", json!({"id":"c"}))
            .await,
        item
    );
    assert_eq!(assistant.items().await, support::seeds());
}

#[tokio::test]
async fn a_request_without_a_verified_token_is_refused_before_it_is_read() {
    let (issuer, _) = support::resource();
    let introspection = authorisation_server(json!({
        "elsewhere": {"active":true, "iss":issuer, "aud":["https://elsewhere.example.com"],
                      "sub":"operator", "scope":"items:read items:write items:remove"},
        "revoked": {"active":false},
    }))
    .await;
    let (_server, endpoint) = serve(&introspection, "development-only").await;
    for token in [None, Some("unknown"), Some("elsewhere"), Some("revoked")] {
        let mut peer = Peer::open(Http::new(&endpoint, token));
        let reply = peer
            .reply(
                "tools/call",
                json!({"name":"items.add", "arguments":{"id":"c", "text":"Add an item"}}),
            )
            .await;
        not_authenticated(reply);
    }
    let mut interactive = connect(&endpoint, "interactive").await;
    assert_eq!(interactive.items().await, support::seeds());

    // Introspection the authorisation server refuses verifies no token.
    let (_server, endpoint) = serve(&introspection, "wrong").await;
    let mut peer = Peer::open(Http::new(&endpoint, Some("interactive")));
    not_authenticated(peer.reply("server/discover", json!({})).await);
}

#[tokio::test]
async fn the_protected_resource_metadata_names_the_authorisation_server() {
    let introspection = authorisation_server(json!({})).await;
    let (_server, endpoint) = serve(&introspection, "development-only").await;
    let metadata = endpoint.replace("/mcp", "/.well-known/oauth-protected-resource");
    let response = reqwest::get(metadata).await.unwrap();
    assert_eq!(response.status(), 200);
    let (issuer, audience) = support::resource();
    assert_eq!(
        response.json::<serde_json::Value>().await.unwrap(),
        json!({"resource":audience, "authorization_servers":[issuer]})
    );
}

#[tokio::test]
async fn only_a_loopback_address_is_served() {
    let introspection = authorisation_server(json!({})).await;
    for (address, diagnostic) in [
        ("0.0.0.0:0", "not a loopback address"),
        ("192.0.2.1:8400", "not a loopback address"),
        ("localhost", "not an address and port"),
    ] {
        let output = launch(&introspection, "development-only", address)
            .output()
            .await
            .unwrap();
        assert_eq!(output.status.code(), Some(1), "{output:?}");
        assert!(output.stdout.is_empty(), "{output:?}");
        let stderr = String::from_utf8(output.stderr).unwrap();
        assert!(stderr.contains(diagnostic), "{stderr}");
    }
}
