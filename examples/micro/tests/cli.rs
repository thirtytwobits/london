//! The list's operations, called through the CLI and through the composed set.

use std::io::{self, Write};
use std::sync::{Arc, Mutex};
use std::time::UNIX_EPOCH;

use clapham::{Cancellation, Category, ComposedSet, OAuthContext, Outcome, RecordSink, Refusal};
use clapham_cli::Cli;
use micro::list::LIST_ID;
use micro::operations::{agent, cli, compose, person, NAME};
use serde_json::{json, Value};
use walthamstow_kernel::WallClock;

/// A clock that stays at the epoch.
fn clock() -> WallClock {
    WallClock::new(|| UNIX_EPOCH)
}

/// A composed set over a fresh workspace, and the directory that holds it.
fn workspace() -> (ComposedSet, tempfile::TempDir) {
    let root = tempfile::tempdir().expect("a workspace directory");
    let set = compose(root.path(), clock()).expect("the list's operations compose");
    (set, root)
}

async fn call(
    set: &ComposedSet,
    auth: OAuthContext,
    name: &str,
    input: Value,
) -> Result<Outcome, Refusal> {
    set.call(
        &set.all(),
        auth,
        name,
        input,
        RecordSink::default(),
        Cancellation::new(),
        false,
    )
    .await
}

async fn result(set: &ComposedSet, auth: OAuthContext, name: &str, input: Value) -> Value {
    call(set, auth, name, input)
        .await
        .unwrap_or_else(|refusal| panic!("{name} is refused: {refusal:?}"))
        .result
        .expect("a result")
}

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// What one invocation of the CLI wrote and the code it exited with.
struct Ran {
    code: u8,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

impl Ran {
    /// The result the invocation wrote, out of its output envelope.
    fn data(&self) -> Value {
        assert_eq!(self.code, 0, "{}", String::from_utf8_lossy(&self.stderr));
        let frame: Value = serde_json::from_slice(&self.stdout).expect("one JSON frame");
        frame["data"].clone()
    }

    /// The refusal the invocation wrote.
    fn refusal(&self) -> Refusal {
        serde_json::from_slice(&self.stderr).expect("one refusal")
    }
}

/// Runs the CLI with `args` after its name, reading `stdin`, with output piped.
async fn run(cli: &Cli, args: &[&str], stdin: &[u8]) -> Ran {
    let stdout = Capture::default();
    let mut stderr = Vec::new();
    let code = cli
        .execute_from(
            std::iter::once(NAME).chain(args.iter().copied()),
            stdin,
            stdout.clone(),
            &mut stderr,
            false,
            Cancellation::new(),
        )
        .await;
    let stdout = stdout.0.lock().unwrap().clone();
    Ran {
        code,
        stdout,
        stderr,
    }
}

fn the_list() -> Value {
    json!({ "entity": "TodoList", "id": LIST_ID })
}

#[tokio::test]
async fn an_operation_run_through_the_cli_has_the_result_the_composed_set_gives() {
    let (set, _root) = workspace();
    result(
        &set,
        person("person"),
        "todo.item.add",
        json!({ "operation_id": "add-milk", "text": "Buy milk" }),
    )
    .await;
    let cli = cli(set.clone(), person("person")).await.expect("the CLI");

    let through_cli = run(&cli, &["todo", "items", "list"], b"").await.data();
    let through_set = result(&set, person("person"), "todo.items.list", json!({})).await;

    assert_eq!(through_cli, through_set);
    assert_eq!(through_cli["snapshot"]["items"][0]["text"], "Buy milk");
}

#[tokio::test]
async fn the_agent_s_stale_write_through_the_cli_is_refused_with_clerkenwell_s_code() {
    let (set, _root) = workspace();
    let added = result(
        &set,
        person("person"),
        "todo.item.add",
        json!({ "operation_id": "add-milk", "text": "Buy milk" }),
    )
    .await;
    let item_id = added["result"]["item_id"].clone();
    let agent_cli = cli(set.clone(), agent("model")).await.expect("the CLI");
    let read = run(
        &agent_cli,
        &[
            "clerkenwell",
            "document",
            "get",
            "--entity",
            "TodoList",
            "--id",
            LIST_ID,
        ],
        b"",
    )
    .await
    .data();

    // The person ticks the item while the agent categorises it from its read.
    result(
        &set,
        person("person"),
        "todo.item.tick",
        json!({ "operation_id": "tick-milk", "item_id": item_id, "done": true }),
    )
    .await;
    let mut categorised = read["document"].clone();
    categorised["items"][0]["category"] = json!("shopping");
    let mut edit = the_list();
    edit["operation_id"] = json!("categorise-1");
    edit["etag"] = read["etag"].clone();
    edit["document"] = categorised;
    let refused = run(
        &agent_cli,
        &["clerkenwell", "document", "edit", "--json", "-"],
        edit.to_string().as_bytes(),
    )
    .await;
    let through_set = call(&set, agent("model"), "clerkenwell.document.edit", edit)
        .await
        .expect_err("the stale write is refused");

    let refusal = refused.refusal();
    assert_eq!(refusal.code, "collaboration_revision");
    assert_eq!(refusal.code, through_set.code);
    assert_eq!(refusal.category, Category::Conflict);
    assert_eq!(refused.code, Category::Conflict.exit_code());
    let listed = result(&set, person("person"), "todo.items.list", json!({})).await;
    assert_eq!(
        listed["snapshot"]["items"],
        json!([{ "item_id": item_id, "text": "Buy milk", "done": true }])
    );
}

#[tokio::test]
async fn a_retried_write_is_answered_as_it_was_accepted_and_not_written_twice() {
    let (set, _root) = workspace();
    let add =
        |operation_id: &str, text: &str| json!({ "operation_id": operation_id, "text": text });
    let milk = result(
        &set,
        person("person"),
        "todo.item.add",
        add("add-milk", "Buy milk"),
    )
    .await;
    let bread = result(
        &set,
        person("person"),
        "todo.item.add",
        add("add-bread", "Buy bread"),
    )
    .await;

    // The retry reads the list after the write it retries and the one after it.
    let retried = result(
        &set,
        person("person"),
        "todo.item.add",
        add("add-milk", "Buy milk"),
    )
    .await;

    assert_eq!(retried["result"]["item_id"], milk["result"]["item_id"]);
    let listed = result(&set, person("person"), "todo.items.list", json!({})).await;
    assert_eq!(
        listed["snapshot"]["items"],
        json!([
            { "item_id": milk["result"]["item_id"], "text": "Buy milk", "done": false },
            { "item_id": bread["result"]["item_id"], "text": "Buy bread", "done": false },
        ])
    );
}
