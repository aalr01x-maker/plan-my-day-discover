"""Claude Haiku 5.5 for news summaries and event classification -- with a hard monthly cap.

Rules (owner, 31 Oct 2026):
  * Model claude-haiku-5-5 ($0.10 / $0.50 per million input / output tokens).
  * HARD cap of $15 per calendar month (UTC). Before every call the worst case
    (input + full max_tokens output) must still fit under the cap, else no call.
    Over the cap the site keeps updating from non-AI sources and earlier summaries.
  * Only genuinely new or materially changed items are sent (results cached by a
    fingerprint of the input), in batches; nothing runs when the site is browsed.
  * The model only summarizes/classifies text it is given. It never adds facts,
    numbers, scores or sources -- prompts say so and outputs are schema-checked.
"""
from __future__ import annotations

import json

from . import common

MODEL = "claude-haiku-5-5"
PRICE_IN = 0.10 / 1_000_000
PRICE_OUT = 0.50 / 1_000_000
MONTHLY_CAP_USD = 15.0
LEDGER = common.STATE / "ai_usage.json"
CACHE = common.STATE / "ai_cache.json"


class Budget:
    def __init__(self, cap: float = MONTHLY_CAP_USD):
        self.cap = cap
        self.month = common.now_utc().strftime("%Y-%m")
        self.data = common.load_json(LEDGER, {})

    @property
    def spent(self) -> float:
        return float(self.data.get(self.month, {}).get("usd", 0.0))

    def allows(self, prompt_chars: int, max_tokens: int) -> bool:
        worst = (prompt_chars / 3) * PRICE_IN + max_tokens * PRICE_OUT
        return self.spent + worst <= self.cap

    def record(self, usage) -> float:
        cost = usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT
        m = self.data.setdefault(self.month, {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0})
        m["usd"] = round(m["usd"] + cost, 6)
        m["calls"] += 1
        m["input_tokens"] += usage.input_tokens
        m["output_tokens"] += usage.output_tokens
        common.save_json(LEDGER, self.data)
        return cost


class AI:
    """Lazily creates the client; `available` is False without a key or over the cap."""

    def __init__(self):
        self.budget = Budget()
        self.cache = common.load_json(CACHE, {})
        self._client = None
        self.disabled_reason = None if common.env("ANTHROPIC_API_KEY") else "no ANTHROPIC_API_KEY"

    @property
    def available(self) -> bool:
        return self.disabled_reason is None

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def json_call(self, key: str, system: str, user: str, schema: dict, max_tokens: int = 4000) -> dict | None:
        """One structured call, cached by `key`. None when unavailable or over budget."""
        if key in self.cache:
            return self.cache[key]
        if not self.available:
            return None
        if not self.budget.allows(len(system) + len(user), max_tokens):
            self.disabled_reason = f"monthly AI cap reached (${self.budget.spent:.2f} of ${self.budget.cap:.2f})"
            common.log(f"AI: {self.disabled_reason}; continuing without AI")
            return None
        import anthropic

        try:
            response = self.client().messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.RateLimitError as exc:
            common.log(f"AI: rate limited ({exc}); skipping this batch")
            return None
        except anthropic.APIStatusError as exc:
            common.log(f"AI: API error {exc.status_code}; skipping this batch")
            if exc.status_code in (401, 403):
                self.disabled_reason = f"API key rejected ({exc.status_code})"
            return None
        except anthropic.APIConnectionError as exc:
            common.log(f"AI: connection error ({exc}); skipping this batch")
            return None
        self.budget.record(response.usage)
        if response.stop_reason != "end_turn":
            common.log(f"AI: stopped with {response.stop_reason} ({key.split(':')[0]}); result discarded")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            common.log("AI: invalid JSON; result discarded")
            return None
        self.cache[key] = data
        return data

    def save(self) -> None:
        # keep the cache bounded (most recent entries win; dicts keep insertion order)
        if len(self.cache) > 4000:
            self.cache = dict(list(self.cache.items())[-4000:])
        common.save_json(CACHE, self.cache)

    def status(self) -> dict:
        return {
            "model": MODEL,
            "month": self.budget.month,
            "spent_usd": round(self.budget.spent, 4),
            "cap_usd": self.budget.cap,
            "active": self.available,
            "note": self.disabled_reason,
        }
