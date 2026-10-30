//! The list, kept by a Clerkenwell store in the workspace's files and served from the bindings
//! generated from `todo.projections.json`.
//!
//! The workspace holds one list, [`LIST_ID`]. Its envelopes are kept under [`LISTS_DIRECTORY`]
//! by Walthamstow's collaboration storage; the store's audit, the peer key and the engine's
//! local records are kept under [`DATA_DIRECTORY`].

use std::num::{NonZeroU32, NonZeroUsize};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use clerkenwell_axum::RpcFailure;
use clerkenwell_events::Principal;
use clerkenwell_schema::CollaborationExchangeMode;
use clerkenwell_session::transport::ProjectionErrorCode;
use clerkenwell_session::{
    AnnouncementFeed, AuthoringStates, Held, ModificationCounter, Modified, NumberedSnapshot,
    ProjectionFailure, ProjectionHost, ProjectionRefusal, ProjectionRegistry,
    ProjectionSubscription,
};
use clerkenwell_store::{
    CollaborationDocumentEdit, CollaborationDocumentId, CollaborationImportRequest,
    CollaborationService, CollaborationStores, CommitPolicy, ImportFence, StoreError,
};
use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use walthamstow_kernel::collaboration_storage::{peer_key, FileCollaborationStorage};
use walthamstow_kernel::durability::WriteDurability;
use walthamstow_kernel::hashing::sha256_hex;
use walthamstow_kernel::WallClock;

use crate::model::{
    GeneratedCollaborationEntitySpec, ItemAddParams, ItemCategoriseParams, ItemTickParams,
    ItemWritten, ItemsListPatch, ItemsListPatchKind, ItemsListSnapshot,
    ProjectionTransportCommandResult, ProjectionTransportMutationResult, ProjectionTransportPatch,
    ProjectionTransportSnapshot, ReplicaUpdateParams, ReplicaUpdateResult, TodoItem,
    GENERATED_COLLABORATION_SPECS, GENERATED_COMMAND_SPECS, GENERATED_ENTITY_AUTHORING_SPECS,
    GENERATED_MUTATION_SPECS, GENERATED_PROJECTION_SPECS, ITEMS_LIST_PROJECTION, ITEM_ADD_MUTATION,
    ITEM_CATEGORISE_MUTATION, ITEM_TICK_MUTATION, LIST_IMPORT_UPDATE_MUTATION, NAMESPACE,
    TODO_LIST_ENTITY,
};

/// The list's collaboration plan.
pub const LIST: &GeneratedCollaborationEntitySpec = &crate::model::TODO_LIST_COLLABORATION_SPEC;

/// The list the workspace holds.
pub const LIST_ID: &str = "list";

/// The store that keeps the list.
pub const LISTS_STORE: &str = "lists";

/// The directory, relative to the workspace root, that keeps the list's envelopes.
pub const LISTS_DIRECTORY: &str = "lists";

/// The directory, relative to the workspace root, that keeps the engine's records.
pub const DATA_DIRECTORY: &str = ".micro";

/// How far each write goes before the store relies on it.
pub const DURABILITY: WriteDurability = WriteDurability::System;

/// Publications a connection may fall behind before it resynchronises.
pub const PUBLICATION_WINDOW: NonZeroUsize = match NonZeroUsize::new(64) {
    Some(window) => window,
    None => panic!("the publication window is non-zero"),
};

/// Commands the server runs at once.
pub const COMMAND_LIMIT: NonZeroUsize = match NonZeroUsize::new(4) {
    Some(limit) => limit,
    None => panic!("the command limit is non-zero"),
};

/// How the store commits.
pub const COMMIT_POLICY: CommitPolicy = CommitPolicy {
    retained_operations: 64,
    attempts: match NonZeroU32::new(8) {
        Some(attempts) => attempts,
        None => panic!("attempts are non-zero"),
    },
    backoff: Duration::from_millis(2),
};

static REGISTRY: ProjectionRegistry = ProjectionRegistry::new(
    NAMESPACE,
    GENERATED_PROJECTION_SPECS,
    GENERATED_MUTATION_SPECS,
    GENERATED_COMMAND_SPECS,
    GENERATED_ENTITY_AUTHORING_SPECS,
);

/// Refuses a list whose items the definition does not describe, or an item without text.
pub fn validate(list: &Value) -> Result<(), StoreError> {
    let items: Vec<TodoItem> = serde_json::from_value(list["items"].clone())
        .map_err(|error| StoreError::invalid_request(error.to_string()))?;
    match items.iter().find(|item| item.text.is_empty()) {
        Some(item) => Err(StoreError::invalid_request(format!(
            "The item {} has no text.",
            item.item_id
        ))),
        None => Ok(()),
    }
}

/// The list kept in a workspace, and the number of each change the store announces.
pub struct List {
    service: CollaborationService,
    authoring: AuthoringStates,
    modifications: Arc<ModificationCounter>,
    /// Each change to the list, as the store announced it.
    announcements: Arc<AnnouncementFeed<()>>,
    /// Held by each mutation from its read to its write, so mutations read what the one
    /// before them wrote.
    writes: tokio::sync::Mutex<()>,
}

impl List {
    /// The list kept in the workspace at `root`, made empty when the workspace holds none. The
    /// store reads the time from `clock`.
    pub fn open(
        root: &Path,
        clock: WallClock,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let data = root.join(DATA_DIRECTORY);
        let storage =
            FileCollaborationStorage::new(&root.join(LISTS_DIRECTORY), DURABILITY, clock)?
                .with_records_at(&data.join("collaboration"))?;
        let stores = CollaborationStores::new(
            GENERATED_COLLABORATION_SPECS,
            COMMIT_POLICY,
            peer_key(&data, DURABILITY)?,
        );
        let service = stores.register(LISTS_STORE, storage)?;
        let modifications = Arc::new(ModificationCounter::default());
        let announcements = Arc::new(AnnouncementFeed::default());
        let (numbered, announced) = (modifications.clone(), announcements.clone());
        // Numbered once the store has saved the change, so a snapshot that reads the number
        // before it reads the list is sent the change.
        stores.changes().listen(move |_| {
            announced.announce(&[TODO_LIST_ENTITY], numbered.take(), ());
        });
        service.bootstrap(
            LIST,
            &list(LIST_ID),
            &json!({ "list_id": LIST_ID, "etag": "", "items": [] }),
            validate,
        )?;
        Ok(Self {
            service,
            authoring: AuthoringStates::new(stores, GENERATED_COLLABORATION_SPECS, |_, _| {
                Some(LISTS_STORE.to_owned())
            }),
            modifications,
            announcements,
            writes: tokio::sync::Mutex::new(()),
        })
    }

    /// Every item on the list, in order.
    fn items(&self) -> Result<Vec<TodoItem>, RpcFailure> {
        Ok(self.read()?.0)
    }

    /// The list's items and the etag they were read at.
    fn read(&self) -> Result<(Vec<TodoItem>, String), RpcFailure> {
        let read = self
            .service
            .read_document(LIST, &list(LIST_ID))
            .map_err(refused)?
            .ok_or_else(|| {
                RpcFailure::new(
                    ProjectionErrorCode::InternalError,
                    "The workspace holds no list.",
                    None,
                )
            })?;
        Ok((decode(read.document["items"].clone())?, read.summary.etag))
    }

    /// Applies `change` to the list as `principal`, as the write `operation_id` the caller
    /// named. The store answers a retry under `operation_id` with the result the write was
    /// accepted with, and refuses a field `principal` may not write.
    async fn write(
        &self,
        principal: &Principal,
        operation_id: String,
        change: impl FnOnce(&mut Vec<TodoItem>) -> Result<(), RpcFailure>,
    ) -> Result<String, RpcFailure> {
        let _writing = self.writes.lock().await;
        let (mut items, etag) = self.read()?;
        change(&mut items)?;
        let written = self
            .service
            .edit(
                LIST,
                CollaborationDocumentEdit {
                    document: list(LIST_ID),
                    operation_id,
                    edited: json!({ "items": items }),
                    etag,
                    actor: principal.clone(),
                    intent: None,
                },
                validate,
            )
            .map_err(refused)?;
        Ok(written.etag)
    }

    /// Adds an item whose id the write's operation id names, so a retry names the same item.
    async fn add(&self, principal: &Principal, params: Value) -> Result<ItemWritten, RpcFailure> {
        let ItemAddParams { operation_id, text } = decode(params)?;
        let item_id = format!("item-{}", &sha256_hex(operation_id.as_bytes())[..16]);
        let added = item_id.clone();
        let etag = self
            .write(principal, operation_id, |items| {
                items.push(TodoItem {
                    item_id: added,
                    text,
                    done: false,
                    category: None,
                });
                Ok(())
            })
            .await?;
        Ok(ItemWritten { item_id, etag })
    }

    async fn tick(&self, principal: &Principal, params: Value) -> Result<ItemWritten, RpcFailure> {
        let ItemTickParams {
            operation_id,
            item_id,
            done,
        } = decode(params)?;
        let etag = self
            .write(principal, operation_id, |items| {
                item(items, &item_id)?.done = done;
                Ok(())
            })
            .await?;
        Ok(ItemWritten { item_id, etag })
    }

    async fn categorise(
        &self,
        principal: &Principal,
        params: Value,
    ) -> Result<ItemWritten, RpcFailure> {
        let ItemCategoriseParams {
            operation_id,
            item_id,
            category,
        } = decode(params)?;
        let etag = self
            .write(principal, operation_id, |items| {
                item(items, &item_id)?.category = category;
                Ok(())
            })
            .await?;
        Ok(ItemWritten { item_id, etag })
    }

    /// Commits `principal`'s operations, made on a replica of the list.
    fn import(
        &self,
        principal: &Principal,
        params: ReplicaUpdateParams,
    ) -> Result<ReplicaUpdateResult, RpcFailure> {
        let base_frontier_base64 = match params.exchange_mode {
            CollaborationExchangeMode::Bootstrap => String::new(),
            CollaborationExchangeMode::Incremental => {
                params.base_frontier_base64.ok_or_else(|| {
                    RpcFailure::new(
                        ProjectionErrorCode::InvalidParams,
                        "An incremental update names the frontier it extends.",
                        None,
                    )
                })?
            }
        };
        let imported = self
            .service
            .import(
                LIST,
                CollaborationImportRequest {
                    document: list(&params.list_id),
                    schema_version: LIST.schema_version,
                    operation_id: params.operation_id,
                    exchange_mode: params.exchange_mode,
                    base_frontier_base64,
                    update_base64: params.update_base64,
                    fence: ImportFence::Frontier,
                    actor: principal.clone(),
                    peer_nonces: params.peer_nonces,
                    intent: params.intent,
                },
                validate,
            )
            .map_err(refused)?;
        Ok(ReplicaUpdateResult {
            list_id: params.list_id,
            etag: imported.etag,
            accepted_frontier_base64: imported.accepted_frontier_base64,
            missing_update_base64: imported.missing_update_base64,
        })
    }

    /// The patch that brings a subscription to the item list to what the store holds now.
    fn items_patch(&self) -> Option<ProjectionTransportPatch> {
        self.items().ok().map(|items| {
            ProjectionTransportPatch::ItemsList(ItemsListPatch {
                kind: ItemsListPatchKind::Reset,
                items,
            })
        })
    }
}

impl ProjectionHost for List {
    type Snapshot = ProjectionTransportSnapshot;
    type Patch = ProjectionTransportPatch;
    type Delivery = ();
    type MutationResult = ProjectionTransportMutationResult;
    type CommandResult = ProjectionTransportCommandResult;
    type Announcement = ();
    type Failure = RpcFailure;

    fn registry(&self) -> &ProjectionRegistry {
        &REGISTRY
    }

    fn authoring_states(&self) -> Option<&AuthoringStates> {
        Some(&self.authoring)
    }

    fn modifications(&self) -> &ModificationCounter {
        &self.modifications
    }

    fn announcements(&self) -> Option<&AnnouncementFeed<()>> {
        Some(&self.announcements)
    }

    async fn snapshot(
        &self,
        projection: &str,
        _params: &Value,
        _held: Option<&()>,
    ) -> Result<NumberedSnapshot<ProjectionTransportSnapshot>, RpcFailure> {
        if projection != ITEMS_LIST_PROJECTION {
            return Err(RpcFailure::new(
                ProjectionErrorCode::UnsupportedProjection,
                format!("The list does not serve \"{projection}\"."),
                None,
            ));
        }
        // Read before the list, so a change saved between the two is sent to the
        // subscription.
        let through = self.modifications.last();
        Ok(NumberedSnapshot {
            snapshot: ProjectionTransportSnapshot::ItemsList(ItemsListSnapshot {
                items: self.items()?,
            }),
            through,
        })
    }

    fn delivered(&self, _snapshot: &ProjectionTransportSnapshot) -> Option<()> {
        None
    }

    fn delivered_by_patch(&self, _patch: &ProjectionTransportPatch) -> Option<()> {
        None
    }

    async fn mutate(
        &self,
        principal: &Principal,
        mutation: &str,
        params: Value,
    ) -> Result<Modified<ProjectionTransportMutationResult>, RpcFailure> {
        let result = match mutation {
            ITEM_ADD_MUTATION => self
                .add(principal, params)
                .await
                .map(ProjectionTransportMutationResult::ItemAdd),
            ITEM_TICK_MUTATION => self
                .tick(principal, params)
                .await
                .map(ProjectionTransportMutationResult::ItemTick),
            ITEM_CATEGORISE_MUTATION => self
                .categorise(principal, params)
                .await
                .map(ProjectionTransportMutationResult::ItemCategorise),
            LIST_IMPORT_UPDATE_MUTATION => self
                .import(principal, decode(params)?)
                .map(ProjectionTransportMutationResult::ListImportUpdate),
            other => Err(RpcFailure::new(
                ProjectionErrorCode::UnsupportedMutation,
                format!("The list does not run \"{other}\"."),
                None,
            )),
        }?;
        // Each change reaches the item list as the store announces it.
        Ok(Modified {
            result,
            modification: None,
        })
    }

    async fn mutation_patch(
        &self,
        _mutation: &str,
        _result: &ProjectionTransportMutationResult,
        _subscription: &ProjectionSubscription<Held<()>>,
    ) -> Option<ProjectionTransportPatch> {
        None
    }

    async fn command(
        &self,
        _principal: &Principal,
        command: &str,
        _params: Value,
    ) -> Result<Modified<ProjectionTransportCommandResult>, RpcFailure> {
        Err(RpcFailure::new(
            ProjectionErrorCode::UnsupportedCommand,
            format!("The list does not run \"{command}\"."),
            None,
        ))
    }

    async fn command_patch(
        &self,
        _command: &str,
        _result: &ProjectionTransportCommandResult,
        _subscription: &ProjectionSubscription<Held<()>>,
    ) -> Option<ProjectionTransportPatch> {
        None
    }

    /// The item list the store's change to the list leaves.
    async fn announcement_patch(
        &self,
        _announcement: &(),
        _subscription: &ProjectionSubscription<Held<()>>,
    ) -> Option<ProjectionTransportPatch> {
        self.items_patch()
    }
}

/// The document of the list `list_id`.
pub fn list(list_id: &str) -> CollaborationDocumentId {
    CollaborationDocumentId::new(TODO_LIST_ENTITY, list_id)
}

/// The item `item_id` among `items`.
fn item<'a>(items: &'a mut [TodoItem], item_id: &str) -> Result<&'a mut TodoItem, RpcFailure> {
    items
        .iter_mut()
        .find(|item| item.item_id == item_id)
        .ok_or_else(|| {
            RpcFailure::new(
                ProjectionErrorCode::NotFound,
                format!("The list holds no item {item_id}."),
                None,
            )
        })
}

fn decode<T: DeserializeOwned>(value: Value) -> Result<T, RpcFailure> {
    serde_json::from_value(value).map_err(|error| {
        RpcFailure::new(ProjectionErrorCode::InvalidParams, error.to_string(), None)
    })
}

fn refused(error: StoreError) -> RpcFailure {
    RpcFailure::refused(ProjectionRefusal::from(error))
}
