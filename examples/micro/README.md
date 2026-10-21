# A to-do list the model sorts

`examples/micro` is the smallest application worth building on the London Project: Clerkenwell,
Walthamstow and one local model, with no web client. It shows an agent the system can refuse,
writing documents that survive a restart.

This example is designed and not yet built.

A person adds items to a list, and a model gives each item a category (Home, Work, Shopping).
Both write the same document. The naive version, which calls the model and overwrites the list
with its answer, silently loses the person's concurrent tick. This example shows each moment
below preventing it.

| Moment | What happens | Piece |
|---|---|---|
| The person ticks an item while the model works | The model's write was made from a stale read; it is refused, and the example re-reads and retries | Clerkenwell: etag fence |
| The person sets a category while the model categorises | The model's category for that item is refused by field name; the person's stands | Clerkenwell: `explicit` for agents |
| The model tries to tick or untick an item | Refused, with nobody racing it | Clerkenwell: `immutable` for agents |
| The model is unsure of an item | No category clears the threshold, so the agent declines and writes nothing | The application, from the readout's probabilities |
| The model's write is retried | Recorded as a duplicate, not written twice | Clerkenwell: client-minted operation id |
| The person stops and restarts the program | The list is still there, read back from files | Walthamstow: workspace, durable writes, journal |
| A second copy opens the same workspace | Refused | Walthamstow: workspace lock |

## The document

| Field | Holds | Conflict policy |
|---|---|---|
| `items` | the ordered list | `merge` |
| `item.text` | what the person wrote | `merge` |
| `item.done` | whether it is ticked | `lastWriterWins` for people; `immutable` for agents |
| `item.category` | the label | `lastWriterWins` for people; `explicit` for agents |

## Shape

- One Rust binary crate and its Clerkenwell definition.
- A scripted walkthrough, in the manner of Clerkenwell's `examples/notes`, and an interactive
  mode.
- The walkthrough holds the model's reply and releases it on cue, so each race lands on the
  same step every run.
- The model sits behind one interface, `decide(state, question, options) -> probabilities`,
  with two implementations: the SemIf port, and a scripted one for tests and CI.

## The model

The agent's model is a Rust port of the direct readout from
[SemIf](https://github.com/TheoLeeCJ/SemIf) (formerly OpenJev), an MIT-licensed open
reproduction of the semantic-if interface pattern.

- Each item is one decision: the item's text is the evidence, "Which category is this item?"
  the criterion, and the categories the options, labelled A, B, C.
- One forward pass over a pinned GGUF reads the next-token logits of the option letters. A
  softmax over them gives each category's probability. Nothing is generated or parsed.
- The agent writes the most probable category only when its probability clears a configured
  threshold. Below it, the agent declines and the item stays uncategorised.
- The port keeps SemIf's prompt and its checks: each option letter is one token, and appending
  a letter leaves the prompt's tokenization unchanged.
- Each decision carries the prompt's SHA-256 and the GGUF's checksum, so a category traces to
  the model and prompt that chose it.

The port runs on llama.cpp through the
[`llama-cpp-2`](https://github.com/utilityai/llama-cpp-rs) bindings. Candle, the pure-Rust
alternative, cannot load Qwen3.5; llama.cpp loads all three candidate architectures (`qwen3`,
`llama`, `qwen35`) and is the path SemIf's own GGUF results come from. Building this example
needs CMake and a C++ compiler beside Cargo.

Battersea sits out: a single model call needs no flow, tool loop or provider registry.

## Open questions

- Which model size? SemIf's balanced accuracy on its own authored decisions:

  | Model | GGUF | Download | Balanced accuracy | Licence |
  |---|---|---|---|---|
  | Qwen3 0.6B | [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF) | 639 MB | 0.44 | Apache-2.0 |
  | MiniCPM5 2B | [openbmb/MiniCPM5-2B-GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF) | 1.56 GB | 0.69 | Apache-2.0 |
  | Qwen3.5 4B | [bartowski/Qwen_Qwen3.5-4B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF) | 3.01 GB | 0.81 | Apache-2.0 |

  Labelling to-do items is easier than those decisions, so 2B may be enough; that is untested.
