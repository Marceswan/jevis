---
name: voice-router
description: "Use when working on the voice-router Hermes plugin."
version: 0.1.0
author: Marc + Atropos
license: MIT
tags: [hermes, plugin, jev, typesafe, voice, stt, routing]
---

# voice-router plugin (Jev intent gate + deep-task dispatch)

Location: `~/.hermes/plugins/voice-router/` (plugin.yaml + __init__.py + jev_client.py). Enabled via `hermes plugins enable voice-router`. Also the reference pattern for any Hermes plugin that calls an MCP tool server.

## What it does
- `pre_llm_call` hook: classifies every user message (>=3 words) with `jev_classify` into command / question / chitchat / deep_task and injects a ROUTING directive for deep_task ("acknowledge briefly, call dispatch_deep, don't work inline") and chitchat ("reply briefly, no tools").
- `dispatch_deep` tool (toolset `voice-router`): records the task to `~/.hermes/logs/voice-router-deep-queue.jsonl` and returns a directive telling the agent to spawn one background `delegate_task` child. The agent genuinely follows it (verified).
- Skips `platform == "subagent"` so delegate children are not routed (they carry their own instructions).
- Fails open: any Jev error logs to `~/.hermes/logs/voice-router.jsonl` and returns None.

## Key learnings
- **Call the Jev MCP server over stdio from the plugin, not the TypeSafe HTTP API.** The wire endpoint (`POST https://api.typesafe.ai/v1/systemone`) uses an internal `state`/`questions` DSL not worth reverse-engineering. Instead spawn `~/.local/bin/jev-mcp-hermes` once as a persistent subprocess and speak JSON-RPC (`initialize` -> `notifications/initialized` -> `tools/call jev_classify`).
- **Warm subprocess latency is ~0.17-0.3s per classify** (vs ~1.5s cold per-call spawn). Keep the client process alive; guard with a threading.Lock.
- **Always give classes explicit `id`s** in jev_classify arguments — descriptions-only returns useless `class0/class1` ids in results.
- **Plugin structure requirements:** directory (not flat .py) with plugin.yaml; `register(ctx)` registers hooks AND tools via `ctx.register_tool(name, toolset, schema, handler)`; opt-in enable with `hermes plugins enable <name>` (config-only write can leave it disabled).
- **Verification pattern:** run `timeout 60 hermes chat -q "<probe>"`, then read the plugin's own jsonl log (the chat process may look hung — it's a REPL). Use `pkill -f "hermes chat -q"` after. Don't wait on stdout.
- Hook contract: `pre_llm_call(session_id, user_message, conversation_history, is_first_turn, model, platform, **kwargs)` returning `{"context": "..."}` or None; injection lands in the user message.
- Routing via hook injection is a soft nudge — but an explicit registered tool (`dispatch_deep`) gives the model a hard, obvious action to take, which is why the deep_task path works reliably.

Default model tiers (Marc's routing defaults, both Nous): fast = DeepSeek 4.1 Flash (the session's configured route — hooks can't hard-swap models mid-turn); slow = GLM 5.3, pinned onto the delegate_task child that dispatch_deep instructs the agent to spawn.

## Phase 2 candidates (not built)
- `jev_extract` to pull structured params from casual speech before dispatch.
- Direct TypeSafe HTTP calls (key from `~/.config/typesafe.env`, never config/argv) if MCP framing latency ever matters.
- Voice-session QA: run `/voice on` in a chat session; plugin needs zero changes for voice since it rides the same LLM loop.
