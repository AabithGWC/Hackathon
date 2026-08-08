"""
Generic Groq LLM client wrapper, reusable across all agent playbooks.
"""
import json
import os
import time

from groq import Groq

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_RATE_LIMIT_RETRIES = 5


class LLMClient:
    def __init__(self, api_key: str = None, model: str = None):
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self.client = Groq(api_key=api_key)
        self.model = model or DEFAULT_MODEL

    def _call_with_backoff(self, **kwargs):
        """Call the chat completion API, retrying with backoff on 429 rate-limit errors."""
        delay = 2.0
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                is_rate_limit = "429" in str(exc) or "rate_limit" in str(exc).lower()
                if not is_rate_limit or attempt == MAX_RATE_LIMIT_RETRIES - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 20.0)

    def complete_json(self, system_prompt: str, user_content: str, temperature: float = 0.2) -> dict:
        """Call the chat completion API in JSON mode and return the parsed dict."""
        response = self._call_with_backoff(
            model=self.model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    def complete_text(self, system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
        """Call the chat completion API and return raw text (no JSON mode)."""
        response = self._call_with_backoff(
            model=self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content
