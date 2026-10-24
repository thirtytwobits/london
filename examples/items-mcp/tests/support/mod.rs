use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

// A protocol peer independent of Clapham's client, with one acknowledged request at a time.
pub struct Peer<R, W> {
    read: BufReader<R>,
    write: W,
    next_id: u64,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> Peer<R, W> {
    pub async fn connect(read: R, write: W) -> Self {
        let mut peer = Self {
            read: BufReader::new(read),
            write,
            next_id: 0,
        };
        let result = peer.request("server/discover", json!({})).await;
        assert!(result["capabilities"]["tools"].is_object(), "{result}");
        peer
    }

    pub async fn request(&mut self, method: &str, mut params: Value) -> Value {
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
        assert!(response.get("error").is_none(), "{response}");
        response["result"].clone()
    }

    pub async fn call(&mut self, name: &str, arguments: Value) -> Value {
        self.request("tools/call", json!({"name":name,"arguments":arguments}))
            .await
    }

    pub async fn items(&mut self) -> Value {
        let result = self.call("items.list", json!({})).await;
        assert_ne!(result["isError"], true, "{result}");
        result["structuredContent"].clone()
    }
}

pub fn seeds() -> Value {
    json!({"items":[
        {"id":"a", "text":"Read a message"},
        {"id":"b", "text":"Write a reply"}
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

pub async fn agent_walkthrough<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(peer: &mut Peer<R, W>) {
    let discovery = peer.request("tools/list", json!({})).await;
    let tools = discovery["tools"].as_array().unwrap();
    let names: Vec<_> = tools
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["items.add", "items.list"]);
    let list = tools
        .iter()
        .find(|tool| tool["name"] == "items.list")
        .unwrap();
    assert_eq!(list["annotations"]["readOnlyHint"], true);
    let add = tools
        .iter()
        .find(|tool| tool["name"] == "items.add")
        .unwrap();
    assert_eq!(add["annotations"]["destructiveHint"], false);
    assert!(add["inputSchema"].is_object());
    assert!(add["outputSchema"].is_object());
    assert_eq!(peer.items().await, seeds());

    let item = json!({"id":"0", "text":"Add an item"});
    let added = peer.call("items.add", item.clone()).await;
    assert_ne!(added["isError"], true, "{added}");
    assert_eq!(added["structuredContent"], item);
    let expected = json!({"items":[item, seeds()["items"][0], seeds()["items"][1]]});
    assert_eq!(peer.items().await, expected);

    let duplicate = peer
        .call("items.add", json!({"id":"0", "text":"Overwrite"}))
        .await;
    refusal(&duplicate, "item_exists", "conflict", "completed");
    assert_eq!(peer.items().await, expected);

    for input in [
        json!({"id":"new"}),
        json!({"id":42,"text":"Invalid"}),
        json!({"id":"", "text":"Invalid"}),
        json!({"id":"new", "text":""}),
        json!({"id":"new", "text":"Valid", "principal":{"kind":"human"}}),
    ] {
        let invalid = peer.call("items.add", input).await;
        refusal(&invalid, "invalid_input", "invalid_input", "not_dispatched");
        assert_eq!(peer.items().await, expected);
    }

    let denied = peer.call("items.remove", json!({"id":"a"})).await;
    refusal(&denied, "not_admitted", "refused", "not_dispatched");
    let spoofed_argument = peer
        .call(
            "items.remove",
            json!({"id":"a", "principal":{"id":"operator", "kind":"human"}}),
        )
        .await;
    refusal(
        &spoofed_argument,
        "invalid_input",
        "invalid_input",
        "not_dispatched",
    );
    let spoofed_metadata = peer
        .request(
            "tools/call",
            json!({
                "name":"items.remove", "arguments":{"id":"a"},
                "_meta":{"principal":{"id":"operator","kind":"human"}}
            }),
        )
        .await;
    refusal(
        &spoofed_metadata,
        "not_admitted",
        "refused",
        "not_dispatched",
    );
    assert_eq!(peer.items().await, expected);
}

pub async fn human_walkthrough<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(peer: &mut Peer<R, W>) {
    let discovery = peer.request("tools/list", json!({})).await;
    let tools = discovery["tools"].as_array().unwrap();
    let names: Vec<_> = tools
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["items.add", "items.list", "items.remove"]);
    assert_eq!(tools[2]["annotations"]["destructiveHint"], true);
    assert_eq!(peer.items().await, seeds());
    let added = peer
        .call("items.add", json!({"id":"c","text":"Human item"}))
        .await;
    assert_eq!(
        added["structuredContent"],
        json!({"id":"c","text":"Human item"})
    );
    let removed = peer.call("items.remove", json!({"id":"a"})).await;
    assert_ne!(removed["isError"], true, "{removed}");
    assert_eq!(removed["structuredContent"], seeds()["items"][0]);
    let expected = json!({"items":[seeds()["items"][1],{"id":"c","text":"Human item"}]});
    assert_eq!(peer.items().await, expected);
    let missing = peer.call("items.remove", json!({"id":"absent"})).await;
    refusal(&missing, "item_not_found", "not_found", "completed");
    assert_eq!(peer.items().await, expected);
}
