"""LLM executor adapter with strict output validation."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

DEFAULT_BASE_URL_ENV = "WINTERMUTE_BASE_URL"
DEFAULT_API_KEY_ENV = "WINTERMUTE_API_KEY"
DEFAULT_MODEL_ENV = "WINTERMUTE_MODEL"


class DecisionError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    type: str
    payload: Dict[str, Any]


def _validate_decision(raw: dict[str, Any]) -> Decision:
    decision_type = raw.get("type")
    if decision_type not in {"yield", "tool", "update", "done", "escalate"}:
        raise DecisionError(f"Invalid decision type: {decision_type}")
    payload = dict(raw)
    payload.pop("type", None)
    if decision_type == "tool":
        if "name" not in payload or "args" not in payload:
            raise DecisionError("Tool decision missing name/args")
    if decision_type == "update":
        if "patch" not in payload:
            raise DecisionError("Update decision missing patch")
    if decision_type == "done":
        if "summary" not in payload:
            raise DecisionError("Done decision missing summary")
    if decision_type == "escalate":
        if "priority" not in payload or "reason" not in payload:
            raise DecisionError("Escalate decision missing priority/reason")
    if decision_type == "yield":
        if "reason" not in payload:
            raise DecisionError("Yield decision missing reason")
    return Decision(decision_type, payload)


def _default_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _build_messages(state: dict[str, Any], observation: dict[str, Any], tool_schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Foreman executor. Respond with a single JSON object for the next action. "
                "Allowed types: yield, tool, update, done, escalate. No extra text."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "state": state,
                    "observation": observation,
                    "tool_schema": tool_schema,
                    "response_contract": {
                        "type": "yield|tool|update|done|escalate",
                        "yield": {"reason": "string"},
                        "tool": {"name": "string", "args": "object"},
                        "update": {"patch": "object"},
                        "done": {"summary": "string"},
                        "escalate": {"priority": "int", "reason": "string"},
                    },
                },
                separators=(",", ":"),
            ),
        },
    ]


class Executor:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url or os.environ.get(DEFAULT_BASE_URL_ENV, "http://localhost:11434/v1")
        self.api_key = api_key or os.environ.get(DEFAULT_API_KEY_ENV, "ollama")
        self.model = model or os.environ.get(DEFAULT_MODEL_ENV, "llama3.2")
        self.timeout_seconds = timeout_seconds

    def decide_next_action(
        self,
        state: dict[str, Any],
        observation: dict[str, Any],
        tool_schema: Iterable[dict[str, Any]],
    ) -> Decision:
        logger = logging.getLogger(__name__)
        logger.info("Executor call model=%s base_url=%s", self.model, self.base_url)
        messages = _build_messages(state, observation, list(tool_schema))
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=_default_headers(self.api_key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except Exception:
            logger.exception("Executor request failed")
            raise
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DecisionError("Model returned non-JSON content") from exc
        decision = _validate_decision(raw)
        logger.info("Executor decision type=%s", decision.type)
        return decision
