import os
import json
import time
from typing import Any, Dict, Optional

import requests

from configs.settings import OPEN_ROUTER_MODEL_ENDPOINT
from configs.settings import OPEN_ROUTER_MODEL_HEADERS


class OpenRouterClient:

    def __init__(
        self,
        model: str = "nvidia/nemotron-3-ultra-550b-a55b:free",
        endpoint: str = OPEN_ROUTER_MODEL_ENDPOINT,
        timeout: int = 120,
        max_tokens: int = 4096,
        retries: int = 2,
    ):
        self.api_key = os.getenv("OPENROUTER_API_KEY")

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY was not loaded")

        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.retries = max(0, retries)

        self.session = requests.Session()
        self.session.headers.update(OPEN_ROUTER_MODEL_HEADERS)

    def generate(
        self,
        system_ins: str,
        state: Any,
    ) -> Dict[str, Any]:

        try:
            system_ins = self._normalize_system(system_ins)
            state = self._normalize_state(state)

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_ins,
                    },
                    {
                        "role": "user",
                        "content": state,
                    },
                ],
                "max_tokens": self.max_tokens,
            }

            raw = self._request(payload)
            content = self._extract_content(raw)

            return {
                "ok": True,
                "content": content,
                "raw": raw,
                "error": None,
            }

        except Exception as exc:
            return {
                "ok": False,
                "content": None,
                "raw": None,
                "error": str(exc),
            }

    @staticmethod
    def _normalize_system(system_ins: Any) -> str:

        if not isinstance(system_ins, str):
            raise TypeError("system_ins must be a string")

        if not system_ins.strip():
            raise ValueError("system_ins cannot be empty")

        return system_ins.strip()

    @staticmethod
    def _normalize_state(state: Any) -> str:

        if state is None:
            raise ValueError("state cannot be None")

        if isinstance(state, str):
            if not state.strip():
                raise ValueError("state cannot be empty")

            return state

        try:
            return json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Unable to serialize state: {exc}"
            ) from exc

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:

        last_error: Optional[Exception] = None

        for attempt in range(self.retries + 1):

            try:
                response = self.session.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout,
                )

            except requests.Timeout as exc:
                last_error = exc

                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue

                raise RuntimeError("OpenRouter request timed out") from exc

            except requests.RequestException as exc:
                raise RuntimeError(
                    f"OpenRouter network error: {exc}"
                ) from exc

            if response.status_code == 401:
                raise RuntimeError(
                    "OpenRouter authentication failed (401)"
                )

            if response.status_code == 429:
                last_error = RuntimeError(
                    f"OpenRouter rate limit (429): {response.text}"
                )

                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue

                raise last_error

            if 500 <= response.status_code < 600:
                last_error = RuntimeError(
                    f"OpenRouter server error "
                    f"({response.status_code}): {response.text}"
                )

                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue

                raise last_error

            if not response.ok:
                raise RuntimeError(
                    f"OpenRouter HTTP {response.status_code}: "
                    f"{response.text}"
                )

            try:
                return response.json()

            except ValueError as exc:
                raise RuntimeError(
                    "OpenRouter returned invalid JSON"
                ) from exc

        raise RuntimeError(str(last_error))

    @staticmethod
    def _extract_content(raw: Dict[str, Any]) -> str:

        try:
            content = raw["choices"][0]["message"]["content"]

        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Malformed OpenRouter response"
            ) from exc

        if not isinstance(content, str):
            raise RuntimeError(
                f"Expected content to be str, "
                f"got {type(content).__name__}"
            )

        return content