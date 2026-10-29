//! The list's operations, composed into one set and assembled into a CLI.
//!
//! The set holds Clerkenwell's document operations and the operations generated from the
//! definition, which its server runs, and Walthamstow's operations over the workspace. The
//! process holds the workspace for as long as the set lives.
//!
//! A grant acts as the principal its subject names, of the kind its [`ACTOR_KIND`] claim
//! names; a grant without one acts as no principal. Every grant reaches the list.

use std::future::{ready, Ready};
use std::path::Path;
use std::sync::Arc;

use clapham::{
    Bounds, ComposeOptions, ComposedSet, Configuration, Contribution, ExecutionMode, Exposure,
    OAuthContext, Refusal,
};
use clapham_cli::{Cli, McpBackend};
use clapham_mcp_server::Server;
use clerkenwell_axum::ProjectionServer;
use clerkenwell_events::{ActorKind, Principal};
use clerkenwell_ops::{document_policies, DocumentScope, Forbidden, Invalid, OperationsHost};
use clerkenwell_store::ENVELOPE_VERSION;
use serde_json::Value;
use walthamstow_kernel::collaboration_storage::declared_envelope_format;
use walthamstow_kernel::local_store::FileLocalStore;
use walthamstow_kernel::workspace_lock::WorkspaceLock;
use walthamstow_kernel::WallClock;
use walthamstow_ops::{Entity, FileWorkspace, FormatStatus, Lease, WalthamstowOps};

use crate::list::{
    validate, List, COMMAND_LIMIT, DATA_DIRECTORY, DURABILITY, LISTS_DIRECTORY, PUBLICATION_WINDOW,
};
use crate::model::TODO_LIST_ENTITY;

/// The name the CLI is invoked as.
pub const NAME: &str = "micro";

/// The issuer every grant the list accepts is verified as issued by.
pub const ISSUER: &str = "https://identity.example.com";

/// The audience every grant the list accepts is issued for.
pub const AUDIENCE: &str = "https://lists.example.com";

/// The claim naming the kind of principal a grant acts as.
pub const ACTOR_KIND: &str = "actor_kind";

/// The scopes a grant needs to read and edit the list and read the workspace.
pub const SCOPES: &[&str] = &[
    clerkenwell_ops::DOCUMENTS_READ,
    clerkenwell_ops::DOCUMENTS_WRITE,
    "todo:read",
    "todo:write",
    walthamstow_ops::operations::WORKSPACE_READ,
    walthamstow_ops::operations::JOURNALS_READ,
];

/// The bytes the in-process connection between the CLI and the composed set buffers.
const CONNECTION_BUFFER: usize = 64 * 1024;

type Error = Box<dyn std::error::Error + Send + Sync>;

/// A grant to `subject`, acting as a person.
pub fn person(subject: &str) -> OAuthContext {
    grant(subject, "human")
}

/// A grant to `subject`, acting as an agent.
pub fn agent(subject: &str) -> OAuthContext {
    grant(subject, "agent")
}

fn grant(subject: &str, kind: &str) -> OAuthContext {
    OAuthContext::from_verified(ISSUER, AUDIENCE, subject, SCOPES.iter().copied())
        .with_claim(ACTOR_KIND, Value::from(kind))
}

/// The list's identity binding and validation.
pub struct ListOperations;

impl OperationsHost for ListOperations {
    fn principal(&self, auth: &OAuthContext) -> Option<Principal> {
        let kind = match auth.claim(ACTOR_KIND)?.as_str()? {
            "human" => ActorKind::Human,
            "agent" => ActorKind::Agent,
            _ => return None,
        };
        Some(Principal::new(auth.subject(), kind))
    }

    fn validate(&self, _entity: &str, document: &Value) -> Result<(), Invalid> {
        validate(document).map_err(|error| Invalid::new(error.message))
    }
}

/// Every grant reaches the list.
fn reach(_auth: OAuthContext, _scope: DocumentScope) -> Ready<Result<(), Forbidden>> {
    ready(Ok(()))
}

/// Whether a list envelope is in the format this build of Clerkenwell reads.
fn envelope_format(_path: &str, bytes: &[u8]) -> FormatStatus {
    match declared_envelope_format(bytes) {
        Some(format) if format == ENVELOPE_VERSION => FormatStatus::Current,
        Some(format) if format > ENVELOPE_VERSION => FormatStatus::Newer {
            format: format.to_string(),
        },
        Some(format) => FormatStatus::Unreadable {
            message: format!("The envelope format {format} is not one this build reads."),
        },
        None => FormatStatus::Unreadable {
            message: "The envelope declares no format.".to_owned(),
        },
    }
}

/// Provides `contribution` and exposes each of its operations.
fn provide(configuration: Configuration, contribution: Contribution) -> Configuration {
    let source = contribution.namespace.clone();
    let names: Vec<String> = contribution
        .operations
        .iter()
        .map(|(operation, _)| operation.name.to_string())
        .collect();
    let mut configuration = configuration.provider(source.clone(), contribution);
    for name in names {
        configuration = configuration.expose(Exposure::new(source.clone(), name));
    }
    configuration
}

/// The list's operations over the workspace at `root`, which the set holds until it is
/// dropped. A workspace another engine holds is refused with Walthamstow's `workspace_held`.
/// The store reads the time from `clock`.
pub fn compose(root: &Path, clock: WallClock) -> Result<ComposedSet, Error> {
    let held = WorkspaceLock::acquire(root)?;
    let data = root.join(DATA_DIRECTORY);
    let server = ProjectionServer::new(List::open(root, clock)?, PUBLICATION_WINDOW, COMMAND_LIMIT);
    let mut configuration = Configuration::new(ISSUER, AUDIENCE);
    for contribution in clerkenwell_ops::contribute(server, Arc::new(ListOperations))? {
        configuration = provide(configuration, contribution);
    }
    let workspace = FileWorkspace::new(root, &data, DURABILITY)
        .directory(TODO_LIST_ENTITY, root.join(LISTS_DIRECTORY));
    let walthamstow = WalthamstowOps::new(
        Lease::daemon(held),
        Arc::new(workspace),
        FileLocalStore::open(&data, DURABILITY)?,
    )
    .entity(Entity::new(
        TODO_LIST_ENTITY,
        LISTS_DIRECTORY,
        envelope_format,
    ));
    configuration = provide(configuration, walthamstow.contribution());
    for (name, policy) in document_policies(reach) {
        configuration = configuration.register_policy(name, policy);
    }
    Ok(configuration.compose_with_options(
        Bounds::default(),
        ComposeOptions {
            execution: ExecutionMode::Daemon,
            ..Default::default()
        },
    )?)
}

/// The CLI over `set`, acting under `grant`. The CLI reaches the set over an in-process MCP
/// connection, which closes when the CLI is dropped.
pub async fn cli(set: ComposedSet, grant: OAuthContext) -> Result<Cli, Refusal> {
    let server = Server::new(set, grant)?;
    let (client, daemon) = tokio::io::duplex(CONNECTION_BUFFER);
    let (read, write) = tokio::io::split(daemon);
    tokio::spawn(async move {
        if let Ok(running) = server.serve_io(read, write).await {
            let _ = running.waiting().await;
        }
    });
    let (read, write) = tokio::io::split(client);
    Cli::connect(
        NAME,
        McpBackend::stdio(read, write).await?,
        Bounds::default(),
    )
    .await
}
