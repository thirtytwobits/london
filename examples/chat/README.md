# A chatbot

`examples/chat` is a chatbot whose conversations are documents a person and an agent both write.
It uses each of Clerkenwell, Walthamstow and Battersea for what that piece owns.

This example is designed and not yet built.

## One message

```mermaid
sequenceDiagram
    participant Client as Web client
    participant C as Clerkenwell
    participant W as Walthamstow
    participant B as Battersea
    participant P as Model provider
    Client->>C: 1. send_message, as the human principal
    C->>W: 2. accepted document written; event published
    W->>B: 3. event starts an activation of flows/chat.flow
    B->>P: 4. prompt, provider loop, tool calls
    P-->>B: reply
    B->>C: 5. reply written as the agent principal, fenced on what the flow read
    C-->>Client: 6. change pushed to every subscribed client
```

## What the person sees

- A list of conversations; start one, rename one.
- Send a message; the reply appears, with the tool step it took.
- Open one conversation in two windows; both update live.
- Restart the server; every conversation is still there, read back from files in the workspace.

## The conversation document

| Field | Holds | Conflict policy |
|---|---|---|
| `title` | the conversation's name | `lastWriterWins` for people; `explicit` for agents, so the agent's auto-title loses to a concurrent rename |
| `messages` | the ordered transcript, each message with its author | `merge`: a person's message and the agent's reply both land |

## What the example proves

Each claim is a test, run in CI against the mock provider.

- The agent's auto-title is refused, by field name, when the person renamed the conversation
  concurrently.
- A reply written from a stale read is fenced, and the flow retries on the accepted document.
- A replayed reply is recorded as a duplicate, not written twice.
- The flow's run is captured as a Pianola roll and graded.

## Stack

| Layer | Choice |
|---|---|
| Server | Rust, `axum` through `clerkenwell-axum` |
| Web build | Vite + TypeScript |
| UI framework | React 18, which `@clerkenwell/react` and `@battersea/editor` declare |
| Components | shadcn/ui on Tailwind CSS |
| Model | `battersea-providers` mock by default; Anthropic and OpenAI by configuration |
| Rust tests | `cargo nextest` |
| Web tests | Vitest unit tests and one Playwright run of the whole chat |

The Rust server is the only system of record. Next.js and React Router's framework mode each
bring a server that would own routes and data beside it.

## Layout

```
server/       Rust: one binary composing the three crates
web/          Vite + React client
definition/   the Clerkenwell definition of a conversation
flows/        the Battersea flow that answers a message
workspace/    a seed workspace the server copies on first run
```

## Open questions

- Should the client show the flow read-only through `@battersea/editor`?
- Which one tool does the agent get? A workspace search over earlier conversations exercises
  Walthamstow; a clock is simpler.
- Should a message's text be streamed into the document as the model answers, or written once
  complete? Streaming shows Clerkenwell's merge policy live but makes the end-to-end test harder
  to keep deterministic.
