use clapham::{
    Bounds, Call, Category, Contribution, Operation, Outcome, Principal, Refusal, compose,
};
use clapham_mcp_server::Server;
use serde_json::{Value, json};
use std::collections::{BTreeMap, btree_map::Entry};
use std::sync::{Arc, Mutex};

pub fn server(principal: Principal) -> Result<Server, Box<dyn std::error::Error>> {
    let items = Arc::new(Mutex::new(BTreeMap::from([
        ("a".to_owned(), "Read a message".to_owned()),
        ("b".to_owned(), "Write a reply".to_owned()),
    ])));
    let item_schema = json!({
        "type":"object", "additionalProperties":false,
        "properties":{"id":{"type":"string","minLength":1},"text":{"type":"string","minLength":1}},
        "required":["id","text"]
    });
    let list: Operation = serde_json::from_value(json!({
        "name":"items.list", "description":"List the items in ID order.",
        "input":{"type":"object","additionalProperties":false},
        "output":{
            "type":"object", "additionalProperties":false,
            "properties":{"items":{"type":"array","items":item_schema}}, "required":["items"]
        },
        "effect":"query", "principals":["human","agent"], "locality":"workspace",
        "idempotent":true, "open_world":false
    }))?;
    let add: Operation = serde_json::from_value(json!({
        "name":"items.add", "description":"Add an item with a new ID and non-empty text. An existing ID is refused.",
        "input":item_schema, "output":item_schema,
        "effect":"mutation", "principals":["human","agent"], "locality":"workspace",
        "refusals":{"item_exists":"conflict"},
        "idempotent":false, "open_world":false
    }))?;
    let remove: Operation = serde_json::from_value(json!({
        "name":"items.remove", "description":"Remove an item by ID. Requires a human principal.",
        "input":{
            "type":"object", "additionalProperties":false,
            "properties":{"id":{"type":"string","minLength":1}}, "required":["id"]
        },
        "output":item_schema,
        "effect":"destructive", "principals":["human"], "locality":"workspace",
        "refusals":{"item_not_found":"not_found"},
        "idempotent":false, "open_world":false
    }))?;

    let list_items = items.clone();
    let add_items = items.clone();
    let contribution = Contribution::new("items")
        .operation(list, move |_: Call| {
            let items = list_items.clone();
            async move {
                let items = items.lock().expect("item lock poisoned");
                let values: Vec<Value> = items
                    .iter()
                    .map(|(id, text)| json!({"id":id,"text":text}))
                    .collect();
                Ok(Outcome::new(json!({"items":values})))
            }
        })
        .operation(add, move |call: Call| {
            let items = add_items.clone();
            async move {
                // The composed runner validates both required strings before dispatch.
                let id = call.input["id"].as_str().expect("validated item ID");
                let text = call.input["text"].as_str().expect("validated item text");
                let mut items = items.lock().expect("item lock poisoned");
                match items.entry(id.to_owned()) {
                    Entry::Occupied(_) => Err(Refusal::new(
                        "item_exists",
                        Category::Conflict,
                        "An item with this ID already exists.",
                    )),
                    Entry::Vacant(entry) => {
                        entry.insert(text.to_owned());
                        Ok(Outcome::new(json!({"id":id,"text":text})))
                    }
                }
            }
        })
        .operation(remove, move |call: Call| {
            let items = items.clone();
            async move {
                let id = call.input["id"].as_str().expect("validated item ID");
                let mut items = items.lock().expect("item lock poisoned");
                match items.remove(id) {
                    Some(text) => Ok(Outcome::new(json!({"id":id,"text":text}))),
                    None => Err(Refusal::new(
                        "item_not_found",
                        Category::NotFound,
                        "No item has this ID.",
                    )),
                }
            }
        });
    let set = compose([contribution], Bounds::default())?;
    // Selecting the domain tools leaves Clapham's internal discovery available to the gate.
    Ok(Server::new(set, principal)?.select(["items.list", "items.add", "items.remove"])?)
}
