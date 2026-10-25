use clapham::{
    Bounds, Call, Category, ComposedSet, Configuration, Contribution, Effect, OAuthContext,
    Refusal, ResourcePolicy, TypedOperation,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, btree_map::Entry};
use std::sync::{Arc, Mutex};

pub const READ: &str = "items:read";
pub const WRITE: &str = "items:write";
pub const REMOVE: &str = "items:remove";
const OWNED: &str = "items.owned";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Item {
    #[schemars(length(min = 1))]
    pub id: String,
    #[schemars(length(min = 1))]
    pub text: String,
    /// The subject of the grant that added the item.
    pub owner: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Items {
    pub items: Vec<Item>,
}

#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ListInput {}

#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AddInput {
    #[schemars(length(min = 1))]
    pub id: String,
    #[schemars(length(min = 1))]
    pub text: String,
}

#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RemoveInput {
    #[schemars(length(min = 1))]
    pub id: String,
}

type Store = Arc<Mutex<BTreeMap<String, Item>>>;

fn seeded() -> Store {
    let item = |id: &str, text: &str| {
        let owner = "operator".to_owned();
        let (id, text) = (id.to_owned(), text.to_owned());
        (id.clone(), Item { id, text, owner })
    };
    Arc::new(Mutex::new(BTreeMap::from([
        item("a", "Read a message"),
        item("b", "Write a reply"),
    ])))
}

fn not_found() -> Refusal {
    Refusal::new("item_not_found", Category::NotFound, "No item has this ID.")
}

/// Compose the item operations with the configuration in `json`, which binds the OAuth
/// resource server and selects the operations exposed.
pub fn compose(json: &str) -> Result<ComposedSet, Box<dyn std::error::Error + Send + Sync>> {
    let items = seeded();
    let list = TypedOperation::<ListInput, Items>::new(
        "items.list",
        "List the items in ID order.",
        Effect::Query,
    )?
    .require_scope(READ)
    .idempotent(true);
    let add = TypedOperation::<AddInput, Item>::new(
        "items.add",
        "Add an item with a new ID and non-empty text. An existing ID is refused.",
        Effect::Mutation,
    )?
    .require_scope(WRITE)
    .refusal("item_exists", Category::Conflict);
    let remove = TypedOperation::<RemoveInput, Item>::new(
        "items.remove",
        "Remove an item by ID. Only the subject that added an item can remove it.",
        Effect::Destructive,
    )?
    .require_scope(REMOVE)
    .policy(OWNED)
    .refusal("item_not_found", Category::NotFound)
    .refusal("not_owner", Category::Refused);

    let (listed, added, removed, owned) = (items.clone(), items.clone(), items.clone(), items);
    let provider = Contribution::new("items")
        .typed_operation(list, move |_: Call, _: ListInput| {
            let items = listed
                .lock()
                .expect("item lock poisoned")
                .values()
                .cloned()
                .collect();
            async move { Ok(Items { items }) }
        })
        .typed_operation(add, move |call: Call, input: AddInput| {
            let result = match added.lock().expect("item lock poisoned").entry(input.id) {
                Entry::Occupied(_) => Err(Refusal::new(
                    "item_exists",
                    Category::Conflict,
                    "An item with this ID already exists.",
                )),
                Entry::Vacant(entry) => {
                    let id = entry.key().clone();
                    let owner = call.auth.subject().to_owned();
                    Ok(entry
                        .insert(Item {
                            id,
                            text: input.text,
                            owner,
                        })
                        .clone())
                }
            };
            async move { result }
        })
        .typed_operation(remove, move |_: Call, input: RemoveInput| {
            let result = removed
                .lock()
                .expect("item lock poisoned")
                .remove(&input.id)
                .ok_or_else(not_found);
            async move { result }
        });
    let policy = ResourcePolicy::typed(move |auth: OAuthContext, input: RemoveInput| {
        let owner = owned
            .lock()
            .expect("item lock poisoned")
            .get(&input.id)
            .map(|item| item.owner.clone());
        let result = match owner {
            None => Err(not_found()),
            Some(owner) if owner != auth.subject() => Err(Refusal::new(
                "not_owner",
                Category::Refused,
                "Another subject added this item.",
            )),
            Some(_) => Ok(()),
        };
        async move { result }
    });
    Ok(Configuration::from_json(json)?
        .register_provider("items", provider)
        .register_policy(OWNED, policy)
        .compose(Bounds::default())?)
}
