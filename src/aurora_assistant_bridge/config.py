"""Bridge configuration.

Precedence: built-in defaults < config file < environment variables < command-line options.
The config file is TOML at the path printed on startup (see config_path()); a commented example is
written there on first run.
"""

import os
import re
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
    llm_max_tokens: int = _setting(0, "LLM_MAX_TOKENS", "maximum tokens per model reply, including reasoning (0 = the server's default)")
    llm_context_tokens: int = _setting(32768, "LLM_CONTEXT_TOKENS", "the model server's context window in tokens (e.g. llama.cpp -c); chat and journal are kept within it")
    llm_timeout_s: int = _setting(600, "LLM_TIMEOUT_S", "seconds before a model call is abandoned")
    tool_mode: str = _setting("native", "TOOL_MODE", "native = OpenAI tool calling; text = JSON action blocks in replies, for models with poor tool calling")

    # Advanced: fixed budgets in characters instead of deriving them from llm_context_tokens.
    chat_budget_chars: int = _setting(0, "CHAT_BUDGET_CHARS", "chat history kept in the model's context, in characters (0 = from llm_context_tokens)")
    journal_budget_chars: int = _setting(0, "JOURNAL_BUDGET_CHARS", "game journal kept in the model's context, in characters (0 = from llm_context_tokens)")
    max_tool_steps: int = _setting(25, "MAX_TOOL_STEPS", "tool calls allowed per turn")

    # Background wake-ups besides player chat. 0 disables.
    wake_journal_entries: int = _setting(15, "WAKE_JOURNAL_ENTRIES", "wake the assistant after this many new journal entries (0 = never)")
    wake_minutes: float = _setting(10.0, "WAKE_MINUTES", "wake the assistant after this many minutes with new activity (0 = never)")

    # Web chat.
    host: str = _setting("127.0.0.1", "BRIDGE_HOST", "address for the chat page; 0.0.0.0 to share it on your network")
    port: int = _setting(47110, "BRIDGE_PORT", "port for the chat page")
    open_browser: bool = _setting(True, "OPEN_BROWSER", "open the chat page in a browser on start")

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


# Settings the chat page may change (the API key is write-only from the page).
UI_SETTINGS = ("llm_base_url", "llm_model", "llm_api_key", "llm_temperature", "llm_max_tokens", "llm_context_tokens")


def env_overridden(key: str) -> bool:
    """True when an environment variable sets this key, so a saved file value would not take effect on restart."""
    f = next(f for f in fields(Config) if f.name == key)
    return f.metadata["env"] in os.environ


def save_settings(updates: dict):
    """Write settings into the config file, keeping its other lines and comments."""
    path = config_path()
    if not path.exists():
        write_example(path)
    lines = path.read_text().splitlines()
    for key, value in updates.items():
        line = f"{key} = {_toml(value)}"
        active = next((i for i, l in enumerate(lines) if re.match(rf"\s*{key}\s*=", l)), None)
        commented = next((i for i, l in enumerate(lines) if re.match(rf"\s*#\s*{key}\s*=", l)), None)
        if active is not None:
            lines[active] = line
        elif commented is not None:
            lines.insert(commented + 1, line)
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n")


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


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
