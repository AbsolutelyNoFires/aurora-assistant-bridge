# Aurora Assistant bridge

This is the client application for the Aurora Assistant api, which is an AuroraPatch mod for [Aurora 4X](http://aurora4x.com/).

This client application provides a chat page with a text interface to talk to an LLM endpoint. It's designed for **local-first** LLM hosting: all calls are sequential, and context length can be managed in-app.

The LLM assistant can play the game with you: reading text boxes, clicking buttons on forms, watching and understanding the wider context as you click between Fuel Efficiency technology menus and the Engine Design screen.

The application also packs its own MCP server, so you can keep working from your preferred harness (Pi, Claude Code, Hermes, etc).

**Version 0.2.3.** Python only: Windows, Linux and macOS users all install it with pip or pipx.

## See it in action

**Answering questions from the game's windows** (1½ min). The model (Qwen 3.8 27B) is asked to review Intelligence data on an alien race, and compare their ship's readings with a similar ship of our own navy. The Qwen model itself is not smart enough to understand that lower TH number in-game = less heat, and reports that our scout is *easier* to spot rather than harder.

<video src="https://github.com/AbsolutelyNoFires/aurora-assistant-bridge/raw/main/docs/media/answers-questions-from-menus.webm" controls width="100%"></video>

[Download the video](https://github.com/AbsolutelyNoFires/aurora-assistant-bridge/raw/main/docs/media/answers-questions-from-menus.webm)

**Giving orders to task groups** (3½ min). Qwen is given a multi-stage command - find tugs, find fuel harvesters, and move the tugs to their gas giant. Qwen needs prompting to locate both classes in the list - at first confusing the Wreckage Harvesters as the intended target - but finally manages to discover the harvesting system, and command the Tug groups to their system ingress.

<video src="https://github.com/AbsolutelyNoFires/aurora-assistant-bridge/raw/main/docs/media/issues-orders-to-task-groups.webm" controls width="100%"></video>

[Download the video](https://github.com/AbsolutelyNoFires/aurora-assistant-bridge/raw/main/docs/media/issues-orders-to-task-groups.webm)

## Install

You need two things: the **game patch** in Aurora, and this **bridge** installed in Python.

**1. The game patch.** Download `aurora-assistant-api-<version>.zip` from the
[aurora-assistant-api releases](https://github.com/AbsolutelyNoFires/aurora-assistant-api/releases) and unzip it into your Aurora
folder, so that `Patches/AuroraAssistantApi/` appears there. (Requires AuroraPatch and its Lib patch.)

**2. The bridge.** Download the newest `aurora_assistant_bridge-<version>-py3-none-any.whl` from the
[releases page](https://github.com/AbsolutelyNoFires/aurora-assistant-bridge/releases). It does *not* go in the Aurora folder —
leave it wherever your browser saved it (usually Downloads) and install it from there. You need Python 3.11 or newer.

*Windows* (Command Prompt; get Python from [python.org](https://www.python.org/downloads/) if `py` is not found):

```bat
py -m pip install "%USERPROFILE%\Downloads\aurora_assistant_bridge-0.2.3-py3-none-any.whl"
py -m aurora_assistant_bridge
```

*Linux / macOS* (uses [pipx](https://pipx.pypa.io/), which installs apps without touching your system Python):

```sh
sudo apt install pipx        # Debian/Ubuntu; macOS: brew install pipx
pipx ensurepath              # then open a new terminal
pipx install ~/Downloads/aurora_assistant_bridge-0.2.3-py3-none-any.whl
aurora-assistant
```

Change `0.2.3` to the version you downloaded. To upgrade later, install the newer file the same way
(with pipx: `pipx install --force <file>`).

## Use

1. Start Aurora through AuroraPatch as usual.
2. Run `aurora-assistant` (Windows: `py -m aurora_assistant_bridge`). The chat opens in your browser at
   <http://localhost:47110/>.
3. Press **Settings** and connect a model (below).

The bridge can be started before or after the game; it connects when the game comes up. Starting it again while it
is running just opens the chat page. To start it with the game on Windows, put `py -m aurora_assistant_bridge` in the
patch's **Command to run when Aurora starts** (AuroraPatch launcher → AuroraAssistantApi → Change settings); on Linux
under Proton the game cannot start Linux programs, so start it from the script that launches Aurora.

### Connect a model

Press **Settings** on the chat page: set the endpoint of any OpenAI-compatible server, press **Load models** (this
also tests the connection) and pick a model from the drop-down (or **Other…** to type a name), and set the
temperature, max tokens per reply and the **context window**.

Set the context window to the server's context size (llama.cpp `-c`, LM Studio "Context Length"; **Detect** reads it
from llama.cpp and LM Studio). The bridge keeps the chat and journal within it, dropping the oldest conversation
first and never the task in progress. If it is set larger than the server really allows, the server silently cuts
the beginning off long prompts and the assistant forgets what it was doing mid-task. Changes apply to the
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
llm_max_tokens = 0            # 0 = the server's default
llm_context_tokens = 32768    # the server's context size, e.g. llama.cpp -c
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
