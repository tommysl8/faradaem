"""Model providers for the strategist, over the standard library only.

Both the Anthropic and OpenAI APIs are plain HTTPS with JSON bodies, so the
clients here use urllib and nothing else. Each client speaks its provider's
wire format but presents one shared shape to the strategist:

    complete(system, messages, tools) -> {"text": str,
                                          "tool_calls": [{"id", "name", "arguments"}],
                                          "stop": "tool_use" | "end"}

messages is a provider-neutral list the strategist maintains:
    {"role": "user" | "assistant", "text": str}
    {"role": "assistant", "tool_calls": [...]}            (a model turn)
    {"role": "tool", "call_id": str, "content": str}      (a tool result)

API keys come from the environment. On Windows the user-scope registry is
consulted as a fallback, so a key set with setx is found even though this
process inherited an older environment. Keys never appear in logs, errors, or
response bodies.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

#: Environment variables holding the keys, per provider.
ANTHROPIC_KEY_VAR = "FARADAEM_ANTHROPIC_KEY"
OPENAI_KEY_VAR = "FARADAEM_OPENAI_KEY"

#: Model overrides, with the defaults used when unset.
ANTHROPIC_MODEL_VAR = "FARADAEM_ANTHROPIC_MODEL"
OPENAI_MODEL_VAR = "FARADAEM_OPENAI_MODEL"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
OPENAI_DEFAULT_MODEL = "gpt-5.6-terra"

#: One request's ceiling on generated tokens. Strategy turns are short.
MAX_TOKENS = 1500

#: Network budget for one model call, in seconds.
REQUEST_TIMEOUT_S = 120.0


class LlmError(RuntimeError):
    """A provider call that failed. The message never contains the key."""


def read_setting(name):
    """An environment value, falling back to the Windows user registry.

    The server process inherits the environment it was launched with, so a
    key set with setx afterward would be invisible. Reading HKCU\\Environment
    directly means a freshly set key works without relaunching anything.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            raw, _ = winreg.QueryValueEx(key, name)
            return str(raw).strip()
    except OSError:
        return ""


#: Transient statuses worth one more try, after a pause.
RETRY_STATUSES = (429, 500, 502, 503, 529)
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT_S = 65.0


def _post_json(url, headers, payload):
    """POST one JSON body and return the decoded JSON response.

    Rate limits and transient server errors are retried a couple of times,
    honouring Retry-After when the provider sends one. Anything else fails
    with the provider's message, never the key.
    """
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                try:
                    wait = float(exc.headers.get("retry-after") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= 0:
                    wait = 5.0 * (attempt + 1)
                time.sleep(min(wait, MAX_RETRY_WAIT_S))
                continue
            _raise_http_error(exc)
        except urllib.error.URLError as exc:
            raise LlmError(
                "Could not reach the model provider: " + str(exc.reason)
            ) from None


def _raise_http_error(exc):
    detail = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        detail = (parsed.get("error") or {}).get("message") or raw[:300]
    except Exception:  # noqa: BLE001 - the error body is best-effort
        pass
    raise LlmError(
        "The model provider returned HTTP " + str(exc.code)
        + (". " + detail if detail else ".")
    ) from None


class AnthropicClient:
    """The Anthropic Messages API."""

    name = "anthropic"
    label = "Claude (Anthropic)"

    def __init__(self, key=None, model=None):
        self.key = key or read_setting(ANTHROPIC_KEY_VAR)
        self.model = model or read_setting(ANTHROPIC_MODEL_VAR) or ANTHROPIC_DEFAULT_MODEL

    def available(self):
        return bool(self.key)

    def build_request(self, system, messages, tools):
        """The request body, as a pure function so tests can pin it."""
        converted = []
        for item in messages:
            if item["role"] == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": item["call_id"],
                        "content": item["content"],
                    }],
                })
            elif item["role"] == "assistant" and "tool_calls" in item:
                content = []
                if item.get("text"):
                    content.append({"type": "text", "text": item["text"]})
                for call in item["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    })
                converted.append({"role": "assistant", "content": content})
            else:
                converted.append({"role": item["role"], "content": item["text"]})

        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": converted,
            "tools": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["schema"],
                }
                for tool in tools
            ],
        }

    def complete(self, system, messages, tools):
        payload = self.build_request(system, messages, tools)
        response = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
            },
            payload,
        )

        text_parts = []
        tool_calls = []
        for block in response.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or "",
                    "name": block.get("name") or "",
                    "arguments": block.get("input") or {},
                })

        return {
            "text": "\n".join(part for part in text_parts if part),
            "tool_calls": tool_calls,
            "stop": "tool_use" if tool_calls else "end",
        }


class OpenAIClient:
    """The OpenAI Chat Completions API."""

    name = "openai"
    label = "GPT (OpenAI)"

    def __init__(self, key=None, model=None):
        self.key = key or read_setting(OPENAI_KEY_VAR)
        self.model = model or read_setting(OPENAI_MODEL_VAR) or OPENAI_DEFAULT_MODEL

    def available(self):
        return bool(self.key)

    def build_request(self, system, messages, tools):
        converted = [{"role": "system", "content": system}]
        for item in messages:
            if item["role"] == "tool":
                converted.append({
                    "role": "tool",
                    "tool_call_id": item["call_id"],
                    "content": item["content"],
                })
            elif item["role"] == "assistant" and "tool_calls" in item:
                converted.append({
                    "role": "assistant",
                    "content": item.get("text") or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for call in item["tool_calls"]
                    ],
                })
            else:
                converted.append({"role": item["role"], "content": item["text"]})

        return {
            "model": self.model,
            # Current OpenAI models take max_completion_tokens; max_tokens is
            # refused with an HTTP 400. And function tools are only accepted
            # on this endpoint with reasoning effort disabled; the strategist
            # is an orchestrator, so that trade is fine.
            "max_completion_tokens": MAX_TOKENS,
            "reasoning_effort": "none",
            "messages": converted,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["schema"],
                    },
                }
                for tool in tools
            ],
        }

    def complete(self, system, messages, tools):
        payload = self.build_request(system, messages, tools)
        response = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": "Bearer " + self.key},
            payload,
        )

        choices = response.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}

        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append({
                "id": call.get("id") or "",
                "name": function.get("name") or "",
                "arguments": arguments,
            })

        return {
            "text": message.get("content") or "",
            "tool_calls": tool_calls,
            "stop": "tool_use" if tool_calls else "end",
        }


class FakeClient:
    """A scripted model for tests and offline verification.

    turns is a list of {"text": ..., "tool_calls": [...]} dicts played back in
    order. The requests it receives are recorded so tests can assert on what
    the strategist actually sent.
    """

    name = "fake"
    label = "Scripted (offline)"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.model = "scripted"

    def available(self):
        return True

    def complete(self, system, messages, tools):
        self.requests.append({
            "system": system,
            "messages": [dict(item) for item in messages],
            "tools": [tool["name"] for tool in tools],
        })
        if not self.turns:
            return {"text": "I have nothing further.", "tool_calls": [], "stop": "end"}
        turn = self.turns.pop(0)
        calls = turn.get("tool_calls") or []
        return {
            "text": turn.get("text") or "",
            "tool_calls": calls,
            "stop": "tool_use" if calls else "end",
        }


def available_providers():
    """The providers that have keys right now, as JSON-ready dicts."""
    listing = []
    for client in (AnthropicClient(), OpenAIClient()):
        if client.available():
            listing.append({
                "name": client.name,
                "label": client.label,
                "model": client.model,
            })
    return listing


def get_client(provider):
    """A live client for one provider name, or an LlmError naming the fix."""
    if provider == "anthropic":
        client = AnthropicClient()
        var = ANTHROPIC_KEY_VAR
    elif provider == "openai":
        client = OpenAIClient()
        var = OPENAI_KEY_VAR
    else:
        raise LlmError(
            "Unknown provider " + repr(provider) + ". Choose anthropic or openai."
        )
    if not client.available():
        raise LlmError(
            "No API key for " + provider + ". Set the " + var
            + " environment variable, for example with setx, and try again."
        )
    return client
