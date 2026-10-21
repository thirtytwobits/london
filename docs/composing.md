# Composing the pieces

An application joins the pieces at a handful of seams. Each seam names what the application
supplies and which piece receives it.

| Seam | The application supplies | Received by |
|---|---|---|
| Documents | a definition of its entities, projections, mutations, commands and conflict policies | Clerkenwell's generator and store |
| Validation | a validator per entity, run on every accepted document | Clerkenwell's store; Walthamstow's kernel |
| Identity | a principal per connection, with its kind | Clerkenwell's store, which attributes and judges each change by it |
| Storage | a workspace root on disk | Walthamstow's workspace and collaboration storage |
| Events | its event types | Walthamstow's event bus |
| Errors | an error type that converts from `KernelError` | Walthamstow's kernel |
| Agent work | node handlers, prompts and product tools | Battersea's runtime, model registry and tool registry |
| Effects | state, effects and durable acceptance, through host interfaces | Battersea's runtime |

## The agent's writes

An agent writes through the same store as a person, under its own principal. Clerkenwell
fences the write on the version the agent read and judges it by each field's policy for agents;
a refusal names the field. When Battersea runs the agent, the application's effect makes that
write.
