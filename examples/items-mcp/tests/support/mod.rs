#![allow(dead_code)]

use axum::{Form, Json, Router, http::HeaderMap, http::StatusCode, routing::post};
use clapham::OAuthContext;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::Path;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

// `items-mcp:development-only`, the resource server's credentials in development-grants.json,
// as HTTP Basic credentials.
const RESOURCE_SERVER: &str = "Basic aXRlbXMtbWNwOmRldmVsb3BtZW50LW9ubHk=";

fn read(file: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(file);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The issuer and audience that configuration.json binds.
pub fn resource() -> (String, String) {
    let oauth = &read("configuration.json")["oauth"];
    let text = |key: &str| oauth[key].as_str().unwrap().to_owned();
    (text("issuer"), text("audience"))
}

/// The grant a development token carries, as introspection would verify it.
pub fn grant(token: &str) -> OAuthContext {
    let grant = &read("development-grants.json")["tokens"][token];
    let (issuer, audience) = resource();
    let scopes = grant["scope"].as_str().unwrap().split(' ');
    OAuthContext::from_verified(issuer, audience, grant["sub"].as_str().unwrap(), scopes)
        .with_client(grant["client_id"].as_str().unwrap())
}

/// Serve RFC 7662 introspection on a loopback port for the development tokens and `extra`,
/// a map from token to introspection response, returning the endpoint's URL.
pub async fn authorisation_server(extra: Value) -> String {
    let (issuer, audience) = resource();
    let mut responses: HashMap<String, Value> = HashMap::new();
    for (token, grant) in read("development-grants.json")["tokens"]
        .as_object()
        .unwrap()
    {
        let mut response = json!({"active":true, "iss":issuer, "aud":[audience]});
        response
            .as_object_mut()
            .unwrap()
            .extend(grant.as_object().unwrap().clone());
        responses.insert(token.clone(), response);
    }
    for (token, response) in extra.as_object().unwrap() {
        responses.insert(token.clone(), response.clone());
    }
    let introspect = move |headers: HeaderMap, Form(form): Form<HashMap<String, String>>| {
        let response = responses.get(&form["token"]).cloned();
        async move {
            if headers.get("authorization").map(|value| value.as_bytes())
                != Some(RESOURCE_SERVER.as_bytes())
            {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({"error":"invalid_client"})),
                );
            }
            (
                StatusCode::OK,
                Json(response.unwrap_or_else(|| json!({"active":false}))),
            )
        }
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new().route("/introspect", post(introspect)),
        )
        .await
        .unwrap();
    });
    format!("http://{address}/introspect")
}

// A protocol peer independent of Clapham's adapters, with one acknowledged request at a time.
pub struct Peer<R, W> {
    read: BufReader<R>,
    write: W,
    next_id: u64,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> Peer<R, W> {
    pub fn open(read: R, write: W) -> Self {
        Self {
            read: BufReader::new(read),
            write,
            next_id: 0,
        }
    }

    pub async fn connect(read: R, write: W) -> Self {
        let mut peer = Self::open(read, write);
        let result = peer.request("server/discover", json!({})).await;
        assert!(result["capabilities"]["tools"].is_object(), "{result}");
        peer
    }

    /// The whole JSON-RPC response to a request.
    pub async fn respond(&mut self, method: &str, mut params: Value) -> Value {
        let meta = params
            .as_object_mut()
            .unwrap()
            .entry("_meta")
            .or_insert(json!({}));
        meta["io.modelcontextprotocol/protocolVersion"] = json!("2026-07-28");
        meta["io.modelcontextprotocol/clientCapabilities"] = json!({});
        meta["io.modelcontextprotocol/clientInfo"] = json!({"name":"items-tests", "version":"1"});
        self.next_id += 1;
        let request = json!({
            "jsonrpc":"2.0", "id":self.next_id, "method":method, "params":params
        });
        let mut bytes = serde_json::to_vec(&request).unwrap();
        bytes.push(b'\n');
        self.write.write_all(&bytes).await.unwrap();
        self.write.flush().await.unwrap();
        let mut line = String::new();
        assert_ne!(
            self.read.read_line(&mut line).await.unwrap(),
            0,
            "server closed before replying"
        );
        let response: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(response["id"], self.next_id, "{response}");
        response
    }

    pub async fn request(&mut self, method: &str, params: Value) -> Value {
        let response = self.respond(method, params).await;
        assert!(response.get("error").is_none(), "{response}");
        response["result"].clone()
    }

    pub async fn tools(&mut self) -> Vec<Value> {
        let discovery = self.request("tools/list", json!({})).await;
        discovery["tools"].as_array().unwrap().clone()
    }

    pub async fn names(&mut self) -> Vec<String> {
        let tools = self.tools().await;
        tools
            .iter()
            .map(|tool| tool["name"].as_str().unwrap().to_owned())
            .collect()
    }

    pub async fn call(&mut self, name: &str, arguments: Value) -> Value {
        self.request("tools/call", json!({"name":name,"arguments":arguments}))
            .await
    }

    pub async fn succeeds(&mut self, name: &str, arguments: Value) -> Value {
        let result = self.call(name, arguments).await;
        assert_ne!(result["isError"], true, "{result}");
        result["structuredContent"].clone()
    }

    pub async fn items(&mut self) -> Value {
        self.succeeds("items.list", json!({})).await
    }
}

pub fn seeds() -> Value {
    json!({"items":[
        {"id":"a", "text":"Read a message", "owner":"operator"},
        {"id":"b", "text":"Write a reply", "owner":"operator"}
    ]})
}

pub fn refusal(result: &Value, code: &str, category: &str, evidence: &str) {
    assert_eq!(result["isError"], true, "{result}");
    let refusal = &result["structuredContent"];
    assert_eq!(refusal["code"], code, "{result}");
    assert_eq!(refusal["category"], category, "{result}");
    assert_eq!(refusal["evidence"], evidence, "{result}");
    assert_eq!(refusal["retryable"], false, "{result}");
}

/// The development token whose walkthrough `walkthrough` runs.
pub const TOKENS: [&str; 3] = ["assistant", "interactive", "colleague"];

pub async fn walkthrough<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    token: &str,
    peer: &mut Peer<R, W>,
) {
    match token {
        "assistant" => assistant(peer).await,
        "interactive" => interactive(peer).await,
        "colleague" => colleague(peer).await,
        _ => panic!("no walkthrough for {token}"),
    }
}

// The assistant grant lacks items:remove, so removal is neither listed nor dispatched.
async fn assistant<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(peer: &mut Peer<R, W>) {
    let tools = peer.tools().await;
    let names: Vec<_> = tools.iter().map(|tool| &tool["name"]).collect();
    assert_eq!(names, ["items.add", "items.list"]);
    assert_eq!(tools[1]["annotations"]["readOnlyHint"], true);
    assert_eq!(tools[0]["annotations"]["destructiveHint"], false);
    assert!(tools[0]["inputSchema"].is_object());
    assert!(tools[0]["outputSchema"].is_object());
    assert_eq!(peer.items().await, seeds());

    let added = peer
        .succeeds("items.add", json!({"id":"c", "text":"Add an item"}))
        .await;
    let item = json!({"id":"c", "text":"Add an item", "owner":"operator"});
    assert_eq!(added, item);
    let expected = json!({"items":[seeds()["items"][0], seeds()["items"][1], item]});
    assert_eq!(peer.items().await, expected);

    let duplicate = peer
        .call("items.add", json!({"id":"c", "text":"Overwrite"}))
        .await;
    refusal(&duplicate, "item_exists", "conflict", "completed");
    for input in [
        json!({"id":"d"}),
        json!({"id":42, "text":"Invalid"}),
        json!({"id":"", "text":"Invalid"}),
        json!({"id":"d", "text":""}),
        json!({"id":"d", "text":"Valid", "owner":"colleague"}),
    ] {
        let invalid = peer.call("items.add", input).await;
        refusal(&invalid, "invalid_input", "invalid_input", "not_dispatched");
    }

    let denied = peer.call("items.remove", json!({"id":"a"})).await;
    refusal(&denied, "insufficient_scope", "refused", "not_dispatched");
    assert_eq!(
        denied["structuredContent"]["details"]["required_scopes"],
        json!(["items:remove"])
    );
    let claimed = peer
        .request(
            "tools/call",
            json!({
                "name":"items.remove", "arguments":{"id":"a"},
                "_meta":{"scope":"items:read items:write items:remove"}
            }),
        )
        .await;
    refusal(&claimed, "insufficient_scope", "refused", "not_dispatched");
    assert_eq!(peer.items().await, expected);
}

// The interactive grant has the same subject as the assistant grant and also items:remove.
async fn interactive<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(peer: &mut Peer<R, W>) {
    let tools = peer.tools().await;
    let names: Vec<_> = tools.iter().map(|tool| &tool["name"]).collect();
    assert_eq!(names, ["items.add", "items.list", "items.remove"]);
    assert_eq!(tools[2]["annotations"]["destructiveHint"], true);
    assert_eq!(peer.items().await, seeds());
    let removed = peer.succeeds("items.remove", json!({"id":"a"})).await;
    assert_eq!(removed, seeds()["items"][0]);
    let missing = peer.call("items.remove", json!({"id":"absent"})).await;
    refusal(&missing, "item_not_found", "not_found", "not_dispatched");
    assert_eq!(peer.items().await, json!({"items":[seeds()["items"][1]]}));
}

// The colleague grant has every scope but another subject, so it removes only its own items.
async fn colleague<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(peer: &mut Peer<R, W>) {
    assert_eq!(
        peer.names().await,
        ["items.add", "items.list", "items.remove"]
    );
    let denied = peer.call("items.remove", json!({"id":"a"})).await;
    refusal(&denied, "not_owner", "refused", "not_dispatched");
    let item = json!({"id":"c", "text":"Add an item", "owner":"colleague"});
    let added = peer
        .succeeds("items.add", json!({"id":"c", "text":"Add an item"}))
        .await;
    assert_eq!(added, item);
    assert_eq!(peer.succeeds("items.remove", json!({"id":"c"})).await, item);
    assert_eq!(peer.items().await, seeds());
}
