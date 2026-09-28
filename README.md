# Aurora Assistant bridge

An LLM assistant for [Aurora 4X](http://aurora2.pentarch.org/) (C# edition). It talks to the game through the
[Aurora Assistant API](http://forgejo/yecenia/aurora-assistant-api) patch and gives you:

- **A chat page** at <http://localhost:47110/>: talk to an assistant that watches what you do in the game (the
  journal), reads any open window, and operates the game for you — e.g. *"design a tanker with 5 EP960 engines and
  enough fuel for 1,000 billion km"*. Works with any OpenAI-compatible model server, local or hosted.
- **An MCP server** exposing the same game tools to MCP clients such as Claude Desktop or Claude Code.

**Version 0.2.0.** Python only: Windows, Linux and macOS users all install it with pip.

## Install

You need the **Aurora Assistant API** patch installed in Aurora (see its README), and Python 3.11+.

```sh
pip install aurora_assistant_bridge-<version>-py3-none-any.whl   # from the releases page
# or from a checkout:
pip install .
```

This installs the `aurora-assistant` command. On Windows, if `aurora-assistant` is not found, run
`python -m aurora_assistant_bridge` instead, or add Python's `Scripts` folder to your PATH.

## Use

1. Start Aurora through AuroraPatch as usual.
2. Run `aurora-assistant`. The chat opens in your browser.

To start it together with the game on Windows, put `aurora-assistant` in the patch's **Command to run when Aurora
starts** (AuroraPatch launcher → AuroraAssistantApi → Change settings). On Linux under Proton the game cannot start
Linux programs, so start `aurora-assistant` from the script that launches Aurora. If a bridge is already running, starting another one
just opens the chat page. The bridge can also be started before the game; it connects when the game comes up.

### Connect a model

Press **Settings** on the chat page: set the endpoint of any OpenAI-compatible server, press **Load models** (this
also tests the connection) and pick a model, and set the temperature and max tokens per reply. Changes apply to the
next message and are saved in the config file.

Or edit the config file directly (run `aurora-assistant config` to see where it is —
`%APPDATA%\aurora-assistant\config.toml` on Windows, `~/.config/aurora-assistant/config.toml` elsewhere) and restart
the bridge:

```toml
# LM Studio
llm_base_url = "http://localhost:1234"
llm_model = "qwen3-27b"

# Ollama:      llm_base_url = "http://localhost:11434"
# llama.cpp:   llm_base_url = "http://localhost:8080"
# OpenRouter:  llm_base_url = "https://openrouter.ai/api"  and  llm_api_key = "sk-or-..."
llm_temperature = 0.6
llm_max_tokens = 0        # 0 = the server's default
```

Any server with an OpenAI-style `/v1/chat/completions` endpoint works. The assistant needs a model that is good at
tool calling; it was developed with Qwen 27B-class models (~150 tokens/s). Small models may struggle with long
multi-step tasks — try `tool_mode = "text"` if a model's native tool calling is unreliable.

Every setting is listed, commented, in the config file; each can also be set with an environment variable
(e.g. `LLM_BASE_URL`) or, for the web settings, on the command line (`--port`, `--host`, `--no-browser`,
`--api-url`).

### The chat page

- One timeline of your chat with the assistant and the game journal (what you, the assistant and the game did,
  with in-game dates). **Chat** / **Journal** toggle each.
- **Stop** (or Esc) stops the assistant, including the model's generation on the server.
- **Clear** starts the assistant with a fresh context.
- Besides answering your messages, the assistant looks at the journal every 15 entries or 10 minutes of activity and
  speaks up if something is worth mentioning (`wake_journal_entries`, `wake_minutes`; 0 disables).

To share the chat on your network (LAN, Tailscale), set `host = "0.0.0.0"`. There is no authentication: anyone who
can open the page can chat, operate the game and change the model settings (but cannot read a saved API key).

## MCP

```sh
aurora-assistant mcp                          # stdio, for clients that launch the server
aurora-assistant mcp --http --port 47111      # streamable HTTP at http://127.0.0.1:47111/mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "aurora": { "command": "aurora-assistant", "args": ["mcp"] } } }
```

Claude Code: `claude mcp add aurora -- aurora-assistant mcp`

Tools: `game_status`, `list_windows`, `read_window`, `click`, `set_value`, `select`, `double_click`,
`answer_dialog`, `recent_activity`. Action tools return their effects (windows opened or closed, message boxes,
changed values), so a client rarely needs to re-read a window. `recent_activity` uses the journal of a running chat
bridge if there is one, otherwise the game's raw UI events.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/aurora-assistant --no-browser
python3 contrib/mock_llm.py 11999    # scripted OpenAI-style server for testing without a model
```

Code: `journal.py` turns API events into journal lines (with per-window extractors), `agent.py` is the assistant
loop (one model call at a time; context = chat interleaved with the journal, truncated separately), `tools.py` the
game tools, `mcp_server.py` the MCP server, `web.py` + `static/index.html` the chat page.
