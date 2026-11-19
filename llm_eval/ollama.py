import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 300.0
PULL_TIMEOUT_SECONDS = 7200.0


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        timeout = self.timeout if timeout is None else timeout
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise OllamaError(f"HTTP {error.code} from {path}: {detail}") from error
        except TimeoutError as error:
            raise OllamaError(f"request to {path} timed out after {timeout}s") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError | socket.timeout):
                raise OllamaError(f"request to {path} timed out after {timeout}s") from error
            raise OllamaError(f"cannot reach {self.base_url}: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise OllamaError(f"invalid JSON from {path}") from error

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/chat", payload)

    def ps(self) -> dict[str, Any]:
        return self._request("GET", "/api/ps")

    def show(self, model: str) -> dict[str, Any]:
        return self._request("POST", "/api/show", {"model": model})

    def pull(self, model: str) -> dict[str, Any]:
        return self._request("POST", "/api/pull", {"model": model, "stream": False}, PULL_TIMEOUT_SECONDS)

    def unload_all(self, attempts: int = 30, poll_seconds: float = 1.0) -> None:
        for entry in self.ps().get("models", []):
            self._request("POST", "/api/generate", {"model": entry.get("name") or entry.get("model"), "keep_alive": 0})
        for _ in range(attempts):
            if not self.ps().get("models"):
                return
            time.sleep(poll_seconds)
        raise OllamaError("models are still loaded after asking Ollama to unload them")
