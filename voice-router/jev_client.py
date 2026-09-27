#!/usr/bin/env python3
"""Minimal MCP stdio client for the Jev server (persistent subprocess)."""
import json
import os
import subprocess
import threading
import queue

WRAPPER = os.path.expanduser("~/.local/bin/jev-mcp-hermes")
NODE_BIN = os.path.expanduser("~/.hermes/tools/node-26.7.0-linux-x64/bin")
INIT_TIMEOUT = 30
CALL_TIMEOUT = 20


class JevClient:
    """Spawn jev-mcp-hermes once, reuse for many classify/decide calls."""

    def __init__(self, wrapper=WRAPPER):
        env = dict(os.environ)
        env["PATH"] = NODE_BIN + os.pathsep + env.get("PATH", "")
        self.proc = subprocess.Popen(
            [wrapper],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=1,
        )
        self._id = 0
        self._lock = threading.Lock()
        self._reader_q = queue.Queue()
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "voice-router", "version": "0.1.0"},
            },
            INIT_TIMEOUT,
        )
        self._notify("notifications/initialized", {})

    def _read_loop(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and ("id" in msg or msg.get("method")):
                self._reader_q.put(msg)

    def _notify(self, method, params):
        with self._lock:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
            self.proc.stdin.flush()

    def _request(self, method, params, timeout):
        with self._lock:
            self._id += 1
            rid = self._id
            self.proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n"
            )
            self.proc.stdin.flush()
        deadline = None
        import time

        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            try:
                msg = self._reader_q.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"jev MCP error: {msg['error']}")
                return msg.get("result")
            seen.append(msg)
        raise TimeoutError(f"jev MCP {method} timed out after {timeout}s")

    def call_tool(self, name, arguments, timeout=CALL_TIMEOUT):
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        content = result.get("content") or []
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"raw": text}

    def classify(self, text, classes, purpose, context=None, timeout=CALL_TIMEOUT):
        payload = {
            "items": [{"id": "u1", "text": text[:2000]}],
            "classes": [{"description": c} for c in classes],
            "purpose": purpose,
            "context": context or "Utterance arrives via live speech-to-text and may contain transcription noise. Judge intent, not grammar.",
        }
        data = self.call_tool("jev_classify", payload, timeout=timeout)
        results = data.get("results") or []
        if not results:
            raise RuntimeError(f"jev_classify returned no results: {data}")
        r = results[0]
        # Class ids come back as provided ids when given, else descriptions; map by description
        return r

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


if __name__ == "__main__":
    import time

    c = JevClient()
    t0 = time.time()
    r = c.classify(
        "hey uh can you spin up a background thing to review my notes and get back to me",
        [
            "A direct request to perform an action or create/change something (task, reminder, message, code change)",
            "A question wanting information or reasoning back",
            "Small talk, acknowledgment, filler - no work needed",
        ],
        "Route a voice utterance to the right conversational handler",
    )
    print(json.dumps(r, indent=2))
    print(f"latency: {time.time() - t0:.2f}s")
    c.close()
