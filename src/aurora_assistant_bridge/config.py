"""Bridge configuration.

Precedence: built-in defaults < config file < environment variables < command-line options.
The config file is TOML at the path printed on startup (see config_path()); a commented example is
written there on first run.
"""

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "aurora-assistant"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aurora-assistant"


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "aurora-assistant"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aurora-assistant"


def config_path() -> Path:
    return Path(os.environ.get("AURORA_ASSISTANT_CONFIG", config_dir() / "config.toml"))


def _setting(default, env: str, doc: str):
    return field(default=default, metadata={"env": env, "doc": doc})


@dataclass
class Config:
    # Aurora Assistant API patch.
    api_url: str = _setting("http://127.0.0.1:47100", "API_URL", "URL of the Aurora Assistant API patch in the game")

    # OpenAI-compatible chat completions endpoint (llama.cpp server, vLLM, Ollama, LM Studio, OpenRouter...).
    llm_base_url: str = _setting("", "LLM_BASE_URL", "OpenAI-compatible server, e.g. http://localhost:1234 (LM Studio) or http://localhost:11434 (Ollama); root or /v1 both work")
    llm_model: str = _setting("local", "LLM_MODEL", "model name as the server knows it")
    llm_api_key: str = _setting("", "LLM_API_KEY", "API key, if the server needs one")
    llm_temperature: float = _setting(0.4, "LLM_TEMPERATURE", "sampling temperature")
    llm_timeout_s: int = _setting(600, "LLM_TIMEOUT_S", "seconds before a model call is abandoned")
    tool_mode: str = _setting("native", "TOOL_MODE", "native = OpenAI tool calling; text = JSON action blocks in replies, for models with poor tool calling")

    # Context budgets in characters (~4 per token); chat and journal are truncated separately.
    chat_budget_chars: int = _setting(24000, "CHAT_BUDGET_CHARS", "chat history kept in the model's context, in characters")
    journal_budget_chars: int = _setting(12000, "JOURNAL_BUDGET_CHARS", "game journal kept in the model's context, in characters")
    max_tool_steps: int = _setting(25, "MAX_TOOL_STEPS", "tool calls allowed per turn")

    # Background wake-ups besides player chat. 0 disables.
    wake_journal_entries: int = _setting(15, "WAKE_JOURNAL_ENTRIES", "wake the assistant after this many new journal entries (0 = never)")
    wake_minutes: float = _setting(10.0, "WAKE_MINUTES", "wake the assistant after this many minutes with new activity (0 = never)")

    # Web chat.
    host: str = _setting("127.0.0.1", "BRIDGE_HOST", "address for the chat page; 0.0.0.0 to share it on your network")
    port: int = _setting(47110, "BRIDGE_PORT", "port for the chat page")
    open_browser: bool = _setting(True, "OPEN_BROWSER", "open the chat page in a browser on start")

    # Optional game desktop streamed over VNC (for Aurora running in a virtual desktop, e.g. on a server):
    # the page then shows the desktop beside the chat. Needs websockify and noVNC.
    vnc_ws_url: str = _setting("", "VNC_WS_URL", "websockify URL, e.g. ws://127.0.0.1:6080/websockify (empty = chat only)")
    novnc_dir: str = _setting("/usr/share/novnc", "NOVNC_DIR", "noVNC web files")

    data_dir: str = _setting(str(default_data_dir()), "DATA_DIR", "where the chat and journal database is kept")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @classmethod
    def load(cls, overrides: dict | None = None) -> "Config":
        cfg = cls()
        path = config_path()
        if path.exists():
            with open(path, "rb") as f:
                for key, value in tomllib.load(f).items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, _coerce(getattr(cfg, key), value))
        for f in fields(cls):
            env = os.environ.get(f.metadata["env"])
            if env is None and f.name == "api_url":
                env = os.environ.get("PATCH_URL")  # older name
            if env is not None:
                setattr(cfg, f.name, _coerce(getattr(cfg, f.name), env))
        for key, value in (overrides or {}).items():
            if value is not None:
                setattr(cfg, key, _coerce(getattr(cfg, key), value))
        return cfg


def _coerce(current, value):
    if isinstance(current, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return str(value)


def write_example(path: Path):
    """A commented config file with every setting and its default."""
    lines = ["# Aurora Assistant bridge settings. Uncomment and edit; restart the bridge to apply.", ""]
    for f in fields(Config):
        default = f.default
        if isinstance(default, bool):
            value = "true" if default else "false"
        elif isinstance(default, (int, float)):
            value = str(default)
        else:
            value = '"' + str(default).replace("\\", "\\\\") + '"'
        lines += [f"# {f.metadata['doc']} (env {f.metadata['env']})", f"# {f.name} = {value}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
