"""Chat pipeline: LLM with tool calling via litellm.

Flow: user message -> LLM -> (optional tool calls -> LLM) -> response.
Max 5 tool-call rounds to prevent infinite loops.

When search/detail tools return actual listings, the output is returned
directly to prevent the LLM from summarizing away data. When no listings
are found, the LLM handles the response naturally (suggestions, alternatives).
"""

import json
import logging
import re

import litellm

from .config import DEEPINFRA_API_KEY, DEEPINFRA_MODEL
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFS, run_tool
from .search_state import merge_search_state, update_search_state
from .session import get_search_state, save_search_state

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_HISTORY = 10

LISTING_TOOLS = {"search_listings"}

_BUDGET_RE = re.compile(
    r"\$[\d,]+|"
    r"\d+\s*k\b|"
    r"\bbudget\b|"
    r"\bunder\b|"
    r"\bover\b|"
    r"\bmax\s*(?:price|budget)?\b|"
    r"\bup\s*to\b|"
    r"\bafford\b|"
    r"\bprice\s*range\b",
    re.I,
)
_BEDROOMS_RE = re.compile(
    r"\d+[\s-]*(?:bed|bedroom|br|bd)|"
    r"\bstudio\b|"
    r"\b(?:one|two|three|four|five)[\s-]*(?:bed|bedroom)",
    re.I,
)


def _recent_user_content(history: list[dict], *, max_user_msgs: int = 4) -> str:
    """Return concatenated content of the last few user turns.

    This allows multi-turn requirement gathering:
    - turn 1: "under 800k"
    - turn 2: "3 bedrooms"
    """
    chunks: list[str] = []
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        chunks.append(str(msg.get("content", "")))
        if len(chunks) >= max_user_msgs:
            break
    chunks.reverse()
    return "\n".join(chunks)


def _recent_user_mentioned(history: list[dict], pattern: re.Pattern) -> bool:  # type: ignore[type-arg]
    """Check pattern in recent user turns (not full stale history)."""
    return bool(pattern.search(_recent_user_content(history)))


def _build_messages(history: list[dict]) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]


def _call_llm(messages: list[dict], use_tools: bool = True):
    kwargs: dict = {
        "model": DEEPINFRA_MODEL,
        "messages": messages,
        "api_key": DEEPINFRA_API_KEY,
        "api_base": "https://api.deepinfra.com/v1/openai",
        "temperature": 0.5,
        "max_tokens": 2048,
    }
    if use_tools:
        kwargs["tools"] = TOOL_DEFS
        kwargs["tool_choice"] = "auto"
    return litellm.completion(**kwargs)


def process_chat(session_id, history: list[dict]) -> str:
    """Run the chat pipeline. Mutates history in place. Returns the assistant reply."""

    messages = _build_messages(history)

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            resp = _call_llm(messages, use_tools=True)
        except Exception as exc:
            log.error("LLM call failed (round %d): %s", _round, exc)
            try:
                resp = _call_llm(messages, use_tools=False)
            except Exception as fallback_exc:
                log.error("LLM fallback also failed: %s", fallback_exc)
                reply = "I'm having trouble connecting right now. Please try again in a moment."
                history.append({"role": "assistant", "content": reply})
                return reply

        msg = resp.choices[0].message  # type: ignore[union-attr]
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            text = (msg.content or "").strip()
            if not text and _round == 0:
                log.warning("Empty LLM response on round 0, retrying")
                continue
            if not text:
                text = "How can I help you with your property search?"
            history.append({"role": "assistant", "content": text})
            return text

        # Record the assistant's tool-call message
        assistant_entry: dict = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        history.append(assistant_entry)
        messages.append(assistant_entry)

        # Execute each tool call
        listing_output = None
        listing_tool_name = None

        for tc in tool_calls:
            search_state = get_search_state(session_id)
            try:
                args = json.loads(tc.function.arguments)
                args = merge_search_state(search_state, tc.function.name, args)
            except (json.JSONDecodeError, TypeError):
                args = {}

            # Hard guard: block search_listings if user never mentioned
            # budget or bedrooms in any message this session.
            if tc.function.name == "search_listings":
                missing = []
                if not _recent_user_mentioned(history, _BUDGET_RE):
                    missing.append("budget (maximum price)")
                if not _recent_user_mentioned(history, _BEDROOMS_RE):
                    missing.append("number of bedrooms")
                if missing:
                    block_msg = (
                        "I still need a few details before I can search: "
                        + " and ".join(missing)
                        + ". Could you let me know?"
                    )
                    log.warning(
                        "Blocked search_listings — user never mentioned: %s",
                        ", ".join(missing),
                    )
                    tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": block_msg}
                    history.append(tool_msg)
                    messages.append(tool_msg)
                    history.append({"role": "assistant", "content": block_msg})
                    return block_msg

            result = run_tool(tc.function.name, args)
            update_search_state(search_state, tc.function.name, args)
            save_search_state(session_id, search_state)
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
            history.append(tool_msg)
            messages.append(tool_msg)

            if tc.function.name in LISTING_TOOLS:
                listing_output = result
                listing_tool_name = tc.function.name

        # When listing tools returned ACTUAL results, return them directly
        # to prevent the LLM from summarizing away the data.
        if listing_output is not None and "Option " in listing_output:
            tail = "\n\nWould you like more details on any of these, or should I adjust the search?"
            reply = listing_output + tail
            history.append({"role": "assistant", "content": reply})
            return reply

        if (
            listing_output is not None
            and listing_tool_name == "search_listings"
            and "couldn't find" in listing_output
        ):
            reply = listing_output
            history.append({"role": "assistant", "content": reply})
            return reply

        # Tool was blocked or non-listing tool — let LLM see the result
        # and generate a natural follow-up (asking for missing info).

    text = (msg.content or "").strip() if msg else ""  # type: ignore[possibly-undefined]
    if not text:
        text = "I've done several lookups — let me know how you'd like to proceed."
    history.append({"role": "assistant", "content": text})
    return text
