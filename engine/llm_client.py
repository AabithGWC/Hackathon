"""
Unified LLM Client supporting both local Ollama (100% free, no rate limits)
and Groq Cloud API with multi-API key failover and key rotation.
"""
import json
import os
import time
import urllib.request
import urllib.error

DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")
PROVIDER = os.environ.get("LLM_PROVIDER", "auto").lower()  # "ollama", "groq", "auto"
MAX_RATE_LIMIT_RETRIES = 3


def is_ollama_available(host: str = OLLAMA_HOST) -> bool:
    """Check if local/remote Ollama server is active and responding."""
    try:
        headers = {}
        if OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"
        req = urllib.request.Request(f"{host}/api/tags", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False



class LLMClient:
    def __init__(self, api_key: str = None, api_keys: list = None, model: str = None, provider: str = None):
        self.provider = (provider or PROVIDER).lower()
        self.ollama_host = OLLAMA_HOST

        # Resolve provider automatically if 'auto'
        if self.provider == "auto":
            if is_ollama_available(self.ollama_host):
                self.provider = "ollama"
                print(f"[LLMClient] Auto-detected local Ollama server at {self.ollama_host}.")
            else:
                self.provider = "groq"

        # Model configuration
        if self.provider == "ollama":
            self.model = model or DEFAULT_OLLAMA_MODEL
        else:
            self.model = model or DEFAULT_GROQ_MODEL

        # Load Groq keys if provider is groq or as secondary fallback
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

        self.api_keys = keys
        self.current_key_idx = 0
        self.expired_keys = set()
        self._groq_clients = {}

        if self.provider == "groq" and not keys:
            raise RuntimeError(
                "No Groq API keys found. Set GROQ_API_KEYS (comma separated) or GROQ_API_KEY in .env, "
                "or start local Ollama server and set LLM_PROVIDER=ollama"
            )

    # --------------------------------------------------------------------------
    # OLLAMA PROVIDER (LOCAL)
    # --------------------------------------------------------------------------
    def _call_ollama(self, messages: list, json_mode: bool = False, temperature: float = 0.2) -> str:
        headers = {"Content-Type": "application/json"}
        if OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

        is_openai_compat = "/v1" in self.ollama_host or "openai" in self.ollama_host

        if is_openai_compat:
            endpoint = f"{self.ollama_host}/chat/completions" if not self.ollama_host.endswith("/chat/completions") else self.ollama_host
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
        else:
            endpoint = f"{self.ollama_host}/api/chat"
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature},
            }
            if json_mode:
                payload["format"] = "json"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if is_openai_compat:
                    return result["choices"][0]["message"]["content"]
                return result.get("message", {}).get("content", "")
        except Exception as exc:
            raise RuntimeError(f"Ollama/Cloud request failed on model '{self.model}' at {endpoint}: {exc}")


    # --------------------------------------------------------------------------
    # GROQ PROVIDER (CLOUD WITH MULTI-KEY ROTATION)
    # --------------------------------------------------------------------------
    def _get_groq_client_for_key(self, key: str):
        from groq import Groq
        if key not in self._groq_clients:
            self._groq_clients[key] = Groq(api_key=key)
        return self._groq_clients[key]

    def _get_next_active_groq_key(self) -> str:
        valid_keys = [k for k in self.api_keys if k not in self.expired_keys]
        if not valid_keys:
            raise RuntimeError("All configured GROQ API keys have expired, reached quota, or failed!")
        selected_key = valid_keys[self.current_key_idx % len(valid_keys)]
        self.current_key_idx += 1
        return selected_key

    def _call_groq_with_backoff(self, **kwargs):
        total_keys = len(self.api_keys)
        attempts = 0

        while attempts < total_keys:
            active_key = self._get_next_active_groq_key()
            client = self._get_groq_client_for_key(active_key)
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
                        print(f"[LLMClient Warning] API Key {active_key[:8]}... failed ({exc}). Switching key...")
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

    # --------------------------------------------------------------------------
    # PUBLIC UNIFIED LLM INTERFACE
    # --------------------------------------------------------------------------
    def complete_json(self, system_prompt: str, user_content: str, temperature: float = 0.2) -> dict:
        """Call LLM in JSON mode and return parsed dict."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        if self.provider == "ollama":
            try:
                raw = self._call_ollama(messages, json_mode=True, temperature=temperature)
                return json.loads(raw)
            except Exception as exc:
                if self.api_keys:
                    print(f"[LLMClient Warning] Ollama call failed ({exc}). Automatically falling back to Groq...")
                    response = self._call_groq_with_backoff(
                        model=DEFAULT_GROQ_MODEL,
                        temperature=temperature,
                        response_format={"type": "json_object"},
                        messages=messages,
                    )
                    raw = response.choices[0].message.content
                    return json.loads(raw)
                raise exc

        # Groq execution
        response = self._call_groq_with_backoff(
            model=self.model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    def complete_text(self, system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
        """Call LLM and return raw text response."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        if self.provider == "ollama":
            try:
                return self._call_ollama(messages, json_mode=False, temperature=temperature)
            except Exception as exc:
                if self.api_keys:
                    print(f"[LLMClient Warning] Ollama call failed ({exc}). Automatically falling back to Groq...")
                    response = self._call_groq_with_backoff(
                        model=DEFAULT_GROQ_MODEL,
                        temperature=temperature,
                        messages=messages,
                    )
                    return response.choices[0].message.content
                raise exc

        # Groq execution
        response = self._call_groq_with_backoff(
            model=self.model,
            temperature=temperature,
            messages=messages,
        )
        return response.choices[0].message.content

