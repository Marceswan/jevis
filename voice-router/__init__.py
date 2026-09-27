"""voice-router: Jev intent gate + deep-task dispatch for Hermes.

Phase 1 scaffold. Behavior:
- pre_llm_call hook: classify each user message with Jev (persistent stdio
  subprocess, warm ~0.3s). Skip trivially short messages. Inject a ROUTING
  directive into the user message when the utterance needs deep work.
- dispatch_deep tool: the agent calls this to record a deep task in a queue
  file; the returned directive instructs it to spawn a background
  delegate_task child (the "slow big model") and keep replying fast.

Fail-open: any Jev error logs and returns None (turn proceeds unrouted).
"""
import json
import os
import time
import threading

from .jev_client import JevClient

LOG_PATH = os.path.expanduser("~/.hermes/logs/voice-router.jsonl")
QUEUE_PATH = os.path.expanduser("~/.hermes/logs/voice-router-deep-queue.jsonl")

CLASSES = [
    {"id": "command", "description": "A direct request to perform an action or create/change something now (task, reminder, message, code change, lookup). Example: 'remind me to call Erik Thursday'"},
    {"id": "question", "description": "A question wanting information, reasoning, or a decision back. Example: 'what did I say about the Sprint retrospective?'"},
    {"id": "chitchat", "description": "Small talk, acknowledgment, filler - no work needed. Example: 'ok cool thanks'"},
    {"id": "deep_task", "description": "A request for substantial multi-step work: research, writing, analysis, code implementation, multi-source synthesis - something that deserves a slower stronger model in the background while the user keeps talking."},
]

PURPOSE = "Route a conversational (often voice/STT) utterance to the right handler tier"
CONTEXT = ("Utterance arrives via live speech-to-text and may contain transcription noise. "
           "Judge intent, not grammar. Prefer chitchat when unsure.")

_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = JevClient()
        return _client


def _log(event):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    event["ts"] = time.time()
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def _classify(message):
    client = _get_client()
    r = client.call_tool("jev_classify", {
        "items": [{"id": "u1", "text": message[:2000]}],
        "classes": CLASSES,
        "purpose": PURPOSE,
        "context": CONTEXT,
    })
    results = r.get("results") or []
    if not results:
        raise RuntimeError(f"jev_classify returned no results: {r}")
    res = results[0]
    return res.get("classification"), res.get("top_probability"), res.get("decision")


def pre_llm_call(session_id, user_message, conversation_history, is_first_turn,
                 model, platform, **kwargs):
    if platform == "subagent":
        return None  # delegate children carry their own instructions; don't route
    if not user_message or len(user_message.split()) < 3:
        return None
    t0 = time.time()
    try:
        label, prob, decision = _classify(user_message)
    except Exception as exc:  # fail-open
        _log({"session": session_id, "event": "error", "error": str(exc)[:300]})
        return None
    _log({
        "session": session_id, "event": "route", "platform": platform,
        "classification": label, "top_probability": prob,
        "jev_decision": decision, "latency_s": round(time.time() - t0, 3),
        "message": user_message[:120],
    })
    if label == "deep_task" and decision == "auto":
        return {
            "context": (
                "ROUTING (voice-router): this utterance was classified as DEEP TASK "
                f"(p={prob:.2f}). Acknowledge it briefly and immediately in your reply, "
                "then call the dispatch_deep tool with the task so the heavy work runs "
                "in a background delegate_task child on a stronger model. Do not do the "
                "deep work inline in this turn."
            )
        }
    if label == "chitchat" and decision == "auto":
        return {
            "context": (
                "ROUTING (voice-router): classified as CHITCHAT (conversational filler). "
                "Reply briefly and naturally; do not start tasks or call tools."
            )
        }
    return None


def _dispatch_deep(task, context=""):
    """Record a deep task and return the directive to run it in background."""
    entry = {"ts": time.time(), "task": task[:2000], "context": str(context)[:2000]}
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    _log({"event": "dispatch_deep", "task": task[:120]})
    return (
        f"DEEP TASK QUEUED: {task}\n\n"
        "Execute now: spawn ONE delegate_task child (background) with this task and "
        "any needed context, pinned to the strong model glm-5.3 on provider zai/nous, "
        "with instructions to deliver a "
        "complete result. Confirm to the user that it is running in the background "
        "and that results will arrive when ready. Do not do the work inline."
    )


def register(ctx):
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_tool(
        name="dispatch_deep",
        toolset="voice-router",
        schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The deep task to run in the background"},
                "context": {"type": "string", "description": "Optional context to pass along"},
            },
            "required": ["task"],
        },
        handler=_dispatch_deep,
    )
