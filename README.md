# jevis

A Jev-powered intent-routing plugin for [Hermes Agent](https://hermes-agent.nousresearch.com) that turns conversational (especially voice/STT) input into a two-tier dispatch: a fast model answers immediately, and heavy work is handed off to a slower, stronger model running in the background.

Built with the [TypeSafe Jev](https://typesafe.ai) judgment API.

## How it works

```
user utterance (typed or STT transcript)
        │
        ▼
Hermes pre_llm_call hook (fires once per turn)
        │
        ▼
jev_classify  ──persistent stdio MCP subprocess──►  TypeSafe Jev
        │
        ├─ chitchat    → "reply briefly, no tools" directive injected
        ├─ question    → no injection (normal fast-model turn)
        ├─ command     → no injection (normal turn)
        └─ deep_task   → "acknowledge briefly, dispatch in background" directive
                              │
                              ▼
                    dispatch_deep tool (registered by the plugin)
                              │
                              ▼
                    Hermes delegate_task child on a stronger model,
                    results arrive between turns
```

Every routing decision is appended to a JSONL log, and the plugin **fails open**: if Jev is unreachable, the turn proceeds unrouted.

### Intent classes

| Class | Meaning | Plugin behavior |
|---|---|---|
| `command` | Direct action request for the current turn | Normal turn |
| `question` | Wants information/reasoning back | Normal turn |
| `chitchat` | Acknowledgment, filler | Injected "reply briefly, no tools" |
| `deep_task` | Multi-step research/writing/analysis/code | Injected background-dispatch directive + `dispatch_deep` tool call |

## Repo contents

- **`voice-router/`** — the Hermes plugin. Drop (or symlink) into `~/.hermes/plugins/voice-router/`:
  - `plugin.yaml` — plugin manifest
  - `__init__.py` — hook + tool registration
  - `jev_client.py` — minimal persistent-subprocess MCP stdio client for the Jev server
- **`skill/SKILL.md`** — the companion Hermes skill documenting architecture, gotchas, and Phase 2 candidates. Install to `~/.hermes/skills/integrations/voice-router/`.

## Prerequisites

1. **Hermes Agent** with plugin support (`hermes plugins list` works).
2. **Jev MCP server** available locally. This plugin spawns a launcher script as a persistent subprocess. The stock launcher used here is:

   ```bash
   # ~/.local/bin/jev-mcp-hermes
   #!/usr/bin/env bash
   # Keeps TYPESAFE_API_KEY out of config.yaml/argv: sourced from a 0600 env file.
   set -a
   source "$HOME/.config/typesafe.env"   # must export TYPESAFE_API_KEY
   set +a
   exec npx -y @jkudish/jev-mcp
   ```

   Create that file (`chmod 700`), put `TYPESAFE_API_KEY=…` in `~/.config/typesafe.env` (`chmod 600`), and adjust the path if your launcher lives elsewhere. The path is configurable at the top of `jev_client.py` (`WRAPPER`).

3. Node/npx available (the MCP server runs via `npx @jkudish/jev-mcp`).

## Installation

```bash
# 1. Install the plugin
git clone https://github.com/Marceswan/jevis.git
mkdir -p ~/.hermes/plugins
ln -s "$(pwd)/jevis/voice-router" ~/.hermes/plugins/voice-router
# or: cp -r jevis/voice-router ~/.hermes/plugins/

# 2. Enable it
hermes plugins enable voice-router

# 3. (Optional) install the companion skill
mkdir -p ~/.hermes/skills/integrations
cp -r jevis/skill ~/.hermes/skills/integrations/voice-router
```

Takes effect on the next Hermes session (a running gateway picks up hooks immediately; tools on next session).

## Verify

```bash
tail -f ~/.hermes/logs/voice-router.jsonl
# in another terminal:
hermes chat -q "ok sounds good thanks"
```

You should see a `route` event with `classification: chitchat`, roughly:

```json
{"event": "route", "platform": "cli", "classification": "chitchat",
 "top_probability": 1.0, "jev_decision": "auto", "latency_s": 0.63, ...}
```

Then try a deep task (`"research X and write up a full recommendation"`), and watch the agent acknowledge immediately and spawn a background `delegate_task` child.

## Design notes

- **MCP over stdio, not raw HTTP.** TypeSafe's wire endpoint (`POST /v1/systemone`) speaks an internal `state`/`questions` DSL. Instead, the plugin spawns the same `jev-mcp-hermes` launcher your MCP config uses once, and issues JSON-RPC `tools/call jev_classify` requests against the warm process. Measured latency: **~0.17–0.3s warm** per classify (vs ~1.5s for a cold per-call spawn).
- **Explicit class ids matter.** `jev_classify` arguments must include `id` fields on classes — descriptions-only classes come back as useless `class0/class1` ids.
- **Soft directive + hard tool.** Hook injection alone is a "nudge" the model may ignore; pairing it with a registered `dispatch_deep` tool gives the model an obvious, concrete action, which makes the background-dispatch path reliable in practice.
- **Subagent-safe.** The hook skips `platform == "subagent"` so `delegate_task` children (who carry their own instructions) are never routed.
- **Secrets hygiene.** The API key lives only in a 0600 env file sourced by the launcher — never in Hermes config, argv, or this repo.

## Phase 2 candidates (not built yet)

- `jev_extract` to pull structured parameters from casual speech before dispatch ("schedule a thing with Erik Thursday" → task/person/date).
- Optional direct TypeSafe HTTP transport if MCP framing latency ever matters.
- Voice-mode QA pass: run `/voice on` in a chat session — the plugin rides the same LLM loop and needs no changes.

## License

MIT
