# JSON Schema that Clerkenwell imports

An application imports a piece's published JSON Schema bundle into its Clerkenwell definition.
Clerkenwell's generator imports the definitions the application's definition reaches, converts
each to its schema subset, and refuses one it holds no form of, naming its path. A refused
definition stops the application's generation, so a type a piece publishes stays within the
forms below.
[`docs/schema-types.md`](https://github.com/thirtytwobits/clerkenwell/blob/{{london:set.clerkenwell.tag}}/docs/schema-types.md)
at the release set's Clerkenwell tag lists the subset, each conversion and each refusal.

## Imported

The schemas `schemars` writes for these serde types import:

- structs, closed with `deny_unknown_fields` or open;
- `Option<T>`, with or without `skip_serializing_if`;
- enums of unit variants, documented or not;
- enums tagged internally, adjacently or externally (serde's default);
- newtypes, `Vec<T>` and `BTreeMap<String, T>`;
- the integer types up to 64 bits, `usize`, `isize`, `f32` and `f64`;
- `serde_json::Value`.

A generated integer is 64-bit signed: a `u64` or `usize` value above `i64::MAX` is outside it.

## Refused

A definition an application reaches is refused when its schema holds:

- an `#[serde(untagged)]` enum;
- a set, written as `uniqueItems`;
- a tuple or fixed-size array, written as `prefixItems`;
- `u128` or `i128`;
- a string `pattern` or `maxLength`, or an array `minItems` or `maxItems`;
- `allOf`, `not`, or `if`, `then` and `else`.

A definition no application reaches is not converted.
