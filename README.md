# AI Station marketing agent: full export (Oct 1, 2026)

Everything the live agent runs on today, pulled from Google Drive and the two scheduled tasks.
`lib/agent_lib.py` is byte-identical to the pinned version (sha256 `a2efd9a6e6315d789906a4fd5e9b804378667ca97c4ca4ab3a8341c638c06165`).

## What's in here

| Path | What it is |
|---|---|
| `lib/agent_lib.py` | The whole engine (~860 lines): Drive-backed state, Telegram polling and sending, MiniMax image generation, Pillow brand renderer (logo embedded as base64), lint, knowledge loader. |
| `data/knowledge.md` | Voice guide + content rules. Claude reads it before writing any copy. |
| `data/state.snapshot.json` | State as of today: lead (Doston Rajabov), Telegram offset, post #20261001, history, msg_map, channel posts. |
| `prompts/daily-post.md` | Scheduled-task prompt: research, write, render, send one package. Daily 08:50 Tashkent. |
| `prompts/telegram-buttons.md` | Scheduled-task prompt: handle ✅ 🎨 ✍️ 💡 buttons and text replies. Hourly :07, 09:07-21:07 Tashkent. |
| `requirements.txt` | Python deps for the library. |

Not included on purpose: `secrets.json` (your MiniMax API key; it's in the Drive folder) and `Montserrat.ttf` (the library downloads it from Google Fonts on GitHub).

## How it runs now (Claude Code cloud routines)

```
Claude Code routine (cloud session, fresh clone of this repo)
   │  git pull → python3 agent.py … → git commit/push data/state.json
   ├── Telegram Bot API   @marketingagent67_bot, leads' private chats only
   ├── MiniMax image-01   one background per platform (brand template on failure)
   └── this repo          data/state.json = memory between runs, data/knowledge.md = voice guide
```

- **Claude does the thinking** (research, picking the idea, writing captions and image prompts, rewriting on request).
- **`agent.py` / `lib/agent_lib.py` do the mechanics**: `check`, `peek`, `start`, `actions`, `knowledge`, `show`, `lint`, `send`, `ack`, `status`, `say`, `learn` (`python3 agent.py -h`).
- Two routines, prompts in `prompts/`: daily post (08:50 Tashkent) and Telegram buttons (hourly :07, 09:07-21:07).
- Each routine run starts from a fresh clone, so it commits `data/state.json` back at the end. That commit is the agent's memory.
- Only the leads' private chats get messages. The public @aistationuz channel (`CHANNEL_ID = -1002832312156`) is read-only.

## Cloud environment setup

1. **Environment variables**: `TELEGRAM_BOT_TOKEN` (from @BotFather for @marketingagent67_bot) and `MINIMAX_API_KEY`.
2. **Network access**: allow `api.telegram.org` and `api.minimax.io` (plus `pypi.org` / `files.pythonhosted.org` for `pip install`).
3. **Git push** from the routine to the branch it runs on, so state is saved.
4. Check with `python3 agent.py check`: both secrets "set" and the bot username printed.

The Montserrat font is vendored in `assets/` (SIL Open Font License), so no font download is needed at run time.

## Switch-over rule

Only one reader may poll the bot. When the new routines go live, disable the two old Claude scheduled tasks ("AIS marketing agent - daily post" and "AIS marketing agent - Telegram buttons") at the same moment, or they will steal each other's button presses. `data/state.json` was seeded from the Oct 1 snapshot; if the old tasks ran after that, copy the latest state.json from Drive over it right before the switch so the offset and post history carry over.

## Files

| Path | What it is |
|---|---|
| `agent.py` | CLI the routines call. |
| `lib/agent_lib.py` | Engine: state, Telegram, MiniMax, Pillow brand renderer, lint, knowledge loader. |
| `data/state.json` | Live state (committed by every run). `state.snapshot.json` is the Oct 1 export it started from. |
| `data/knowledge.md` | Voice guide + content rules. |
| `prompts/*.md` | The two routine prompts. |
| `assets/Montserrat.ttf` | Brand font. |
