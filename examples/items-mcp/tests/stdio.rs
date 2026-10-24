mod support;

use std::process::Stdio;
use support::{Peer, agent_walkthrough, human_walkthrough};
use tokio::process::Command;

#[tokio::test]
async fn subprocess_walkthrough_and_clean_shutdown() {
    for kind in ["agent", "human"] {
        let mut child = Command::new(env!("CARGO_BIN_EXE_items-mcp"))
            .args(["--principal", kind])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let mut peer =
            Peer::connect(child.stdout.take().unwrap(), child.stdin.take().unwrap()).await;
        if kind == "agent" {
            agent_walkthrough(&mut peer).await;
        } else {
            human_walkthrough(&mut peer).await;
        }
        drop(peer);
        let output = child.wait_with_output().await.unwrap();
        assert!(output.status.success(), "{output:?}");
        assert!(output.stderr.is_empty(), "{output:?}");
    }
}

#[test]
fn launch_configuration_is_explicit() {
    for args in [
        vec![],
        vec!["--principal", "system"],
        vec!["--principal", "agent", "extra"],
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
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains("--principal")
    );
}
