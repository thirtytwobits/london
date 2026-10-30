//! A to-do list a person and a model both edit, on Clerkenwell, Walthamstow and Clapham.
//!
//! `list` serves the list from a Clerkenwell store kept in a Walthamstow workspace, and
//! `operations` composes Clerkenwell's and Walthamstow's operations over it into one set and
//! assembles the CLI from that set.

pub mod list;
/// The Rust bindings `clerkenwell-codegen` generates from `todo.projections.json`.
pub mod model;
pub mod operations;
