"""Bridge configuration, read from environment variables (see bridge/companion.env.example)."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # In-game Companion patch API.
    patch_url: str = field(default_factory=lambda: _env("PATCH_URL", "http://127.0.0.1:47100"))

    # OpenAI-compatible chat completions endpoint (llama.cpp server, vLLM, Ollama, LM Studio...).
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "").rstrip("/"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "local"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.4")))
    llm_timeout_s: int = field(default_factory=lambda: int(_env("LLM_TIMEOUT_S", "600")))
    # Some local servers/models handle native tool calling poorly; "text" makes the model emit JSON actions instead.
    tool_mode: str = field(default_factory=lambda: _env("TOOL_MODE", "native"))

    # Context budgets, in characters (roughly 4 per token), truncated separately.
    chat_budget_chars: int = field(default_factory=lambda: int(_env("CHAT_BUDGET_CHARS", "24000")))
    journal_budget_chars: int = field(default_factory=lambda: int(_env("JOURNAL_BUDGET_CHARS", "12000")))
    max_tool_steps: int = field(default_factory=lambda: int(_env("MAX_TOOL_STEPS", "25")))

    # Wake-up triggers besides player chat. 0 disables.
    wake_journal_entries: int = field(default_factory=lambda: int(_env("WAKE_JOURNAL_ENTRIES", "15")))
    wake_minutes: float = field(default_factory=lambda: float(_env("WAKE_MINUTES", "10")))

    # Web UI.
    host: str = field(default_factory=lambda: _env("BRIDGE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("BRIDGE_PORT", "80")))
    # websockify (VNC over websocket), proxied by the bridge at /websockify.
    vnc_ws_url: str = field(default_factory=lambda: _env("VNC_WS_URL", "ws://127.0.0.1:6080/websockify"))

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(Path.home() / ".local/share/aurora-companion"))))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)
