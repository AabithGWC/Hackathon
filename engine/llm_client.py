"""
Generic Groq LLM client wrapper with multi-API key rotation and automatic failover, reusable across all agent playbooks.
"""
import json
import os
import time

from groq import Groq

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_RATE_LIMIT_RETRIES = 3


class LLMClient:
    def __init__(self, api_key: str = None, api_keys: list = None, model: str = None):
        """
        Supports passing a single api_key, a list of api_keys, or loading from env variables:
        - GROQ_API_KEYS (comma-separated list of keys, e.g. key1,key2,key3)
        - GROQ_API_KEY (single fallback key)
        """
        keys = []
        if api_keys:
            keys = [k.strip() for k in api_keys if k and k.strip()]
        elif api_key:
            keys = [api_key.strip()]
        else:
            env_keys = os.environ.get("GROQ_API_KEYS", "")
            single_key = os.environ.get("GROQ_API_KEY", "")
            if env_keys:
                keys = [k.strip() for k in env_keys.split(",") if k.strip()]
            elif single_key:
                keys = [single_key.strip()]

        if not keys:
            raise RuntimeError(
                "No Groq API keys found. Set GROQ_API_KEYS (comma separated) or GROQ_API_KEY in .env"
            )

        self.api_keys = keys
        self.model = model or DEFAULT_MODEL
        self.current_key_idx = 0
        self.expired_keys = set()
        self._clients = {}

    def _get_client_for_key(self, key: str) -> Groq:
        if key not in self._clients:
            self._clients[key] = Groq(api_key=key)
        return self._clients[key]

    def _get_next_active_key(self) -> str:
        """Returns the next valid API key, skipping expired ones in round-robin fashion."""
        valid_keys = [k for k in self.api_keys if k not in self.expired_keys]
        if not valid_keys:
            raise RuntimeError("All configured GROQ API keys have expired, reached quota, or failed!")
        
        selected_key = valid_keys[self.current_key_idx % len(valid_keys)]
        self.current_key_idx += 1
        return selected_key

    def _call_with_backoff(self, **kwargs):
        """
        Calls the chat completion API, trying available API keys with automatic failover
        if a key expires (401/403), exceeds quota, or hits severe rate limits.
        """
        total_keys = len(self.api_keys)
        attempts = 0

        while attempts < total_keys:
            active_key = self._get_next_active_key()
            client = self._get_client_for_key(active_key)
            
            delay = 1.5
            for attempt in range(MAX_RATE_LIMIT_RETRIES):
                try:
                    return client.chat.completions.create(**kwargs)
                except Exception as exc:
                    err_msg = str(exc).lower()
                    is_expired_or_auth = any(code in err_msg for code in ["401", "403", "unauthorized", "invalid api key", "invalid_api_key"])
                    is_quota = "quota" in err_msg or "exceeded" in err_msg or "billing" in err_msg
                    is_rate_limit = "429" in err_msg or "rate limit" in err_msg or "rate_limit" in err_msg

                    if is_expired_or_auth or is_quota:
                        print(f"[LLMClient Warning] API Key {active_key[:8]}... failed ({exc}). Marking key as expired and trying next key...")
                        self.expired_keys.add(active_key)
                        break

                    if is_rate_limit:
                        if attempt < MAX_RATE_LIMIT_RETRIES - 1:
                            time.sleep(delay)
                            delay = min(delay * 2, 10.0)
                            continue
                        else:
                            print(f"[LLMClient Warning] API Key {active_key[:8]}... hit rate limit. Switching key...")
                            break

                    raise exc

            attempts += 1

        raise RuntimeError("All available Groq API keys failed or were exhausted.")

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

