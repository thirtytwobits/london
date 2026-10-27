#[path = "../tests/support/mod.rs"]
mod support;

use clapham::OAuthContext;
use clapham_mcp_server::Server;
use serde_json::json;
use support::{Peer, Streams, TOKENS, grant, walkthrough};
use tokio::io::{DuplexStream, ReadHalf, WriteHalf};

fn compose() -> clapham::ComposedSet {
    let json = include_str!("../configuration.json");
    crate::application::compose(json).unwrap()
}

type Connection = Peer<Streams<ReadHalf<DuplexStream>, WriteHalf<DuplexStream>>>;

// The server task ends with an error if the client leaves before discovery succeeds.
async fn open(
    authority: OAuthContext,
) -> (Connection, tokio::task::JoinHandle<Result<(), String>>) {
    let set = compose();
    let items: Vec<_> = set
        .operations()
        .map(|operation| operation.name.to_string())
        .filter(|name| name.starts_with("items."))
        .collect();
    let server = Server::new(set, authority).unwrap().select(items).unwrap();
    let (client, transport) = tokio::io::duplex(65536);
    let (read, write) = tokio::io::split(transport);
    let running = tokio::spawn(async move {
        let running = server
            .serve_io(read, write)
            .await
            .map_err(|error| error.to_string())?;
        running.waiting().await.map_err(|error| error.to_string())?;
        Ok(())
    });
    let (read, write) = tokio::io::split(client);
    (Peer::open(Streams::new(read, write)), running)
}

#[tokio::test]
async fn each_development_grant_walks_through_its_permissions() {
    for token in TOKENS {
        let (mut peer, running) = open(grant(token)).await;
        peer.request("server/discover", json!({})).await;
        walkthrough(token, &mut peer).await;
        drop(peer);
        running.await.unwrap().unwrap();
    }
}

#[tokio::test]
async fn a_grant_for_another_resource_server_is_refused_discovery() {
    let (issuer, _) = support::resource();
    let elsewhere = OAuthContext::from_verified(
        issuer,
        "https://elsewhere.example.com",
        "operator",
        ["items:read", "items:write", "items:remove"],
    );
    let (mut peer, running) = open(elsewhere).await;
    let refused = peer.respond("server/discover", json!({})).await;
    assert_eq!(
        refused["error"]["data"]["code"], "not_authenticated",
        "{refused}"
    );
    drop(peer);
    assert!(running.await.unwrap().is_err());
}
