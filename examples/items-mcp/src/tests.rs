#[path = "../tests/support/mod.rs"]
mod support;

use clapham::{Principal, PrincipalKind};
use support::{Peer, agent_walkthrough, human_walkthrough};

#[tokio::test]
async fn agent_mcp_contract() {
    exercise(PrincipalKind::Agent).await;
}

#[tokio::test]
async fn human_mcp_contract() {
    exercise(PrincipalKind::Human).await;
}

async fn exercise(kind: PrincipalKind) {
    let server = crate::application::server(Principal::new("operator", kind)).unwrap();
    let (client, transport) = tokio::io::duplex(65536);
    let (read, write) = tokio::io::split(transport);
    let running = tokio::spawn(async move {
        server
            .serve_io(read, write)
            .await
            .unwrap()
            .waiting()
            .await
            .unwrap();
    });
    let (read, write) = tokio::io::split(client);
    let mut peer = Peer::connect(read, write).await;
    if kind == PrincipalKind::Agent {
        agent_walkthrough(&mut peer).await;
    } else {
        human_walkthrough(&mut peer).await;
    }
    drop(peer);
    running.await.unwrap();
}
