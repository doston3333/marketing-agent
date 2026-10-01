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

## How it runs today

```
Claude scheduled task ──> Composio workbench ──exec()──> agent_lib.py (from Drive, hash-checked)
                                                     │
          ┌──────────────────┬───────────────────────┼─────────────────────┐
     Google Drive        Telegram bot           MiniMax image-01       Composio upload
  (state, knowledge,  @aistation_poster_bot     (1 background per     (public URL for
   secrets, font)     leads' private chats)      platform)             sendPhoto)
```

- **Claude does the thinking** (research, picking the idea, writing captions and image prompts, rewriting on request).
- **The library does the mechanics** (`poll`, `take_actions`, `send_package`, `render_all`, `compose`, `lint`, `save_state` with merge).
- Only the leads' private chats get messages. The public @aistationuz channel (`CHANNEL_ID = -1002832312156`) is read-only for the agent.

## Porting to Claude Code: the 3 things to replace

The library expects three globals that only exist inside the Composio workbench:

| Global | Used for | Replace with |
|---|---|---|
| `proxy_execute(method, url, toolkit="googledrive", ...)` (5 uses) | Drive read/write of state.json, knowledge.md, secrets.json | Local files (`data/`) or the Google Drive API with your own OAuth |
| `run_composio_tool("TELEGRAM_*", args)` (2 uses, via `tg()` and `poll()`) | getUpdates, sendMessage, sendPhoto, answerCallbackQuery | Direct Bot API calls: `requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=args)`. Slugs map 1:1 (`TELEGRAM_SEND_PHOTO` → `sendPhoto`, etc.) |
| `upload_local_file(path)` (2 uses, in `public_url()`) | Getting a public URL for each image so Telegram can fetch it | Skip it: send the file directly with `sendPhoto` as multipart (`files={"photo": open(path,"rb")}`) |

Everything else (rendering, lint, state merge, button handling) is plain Python and works as is.

Secrets you'll need in the new setup: the Telegram bot token for @aistation_poster_bot (from @BotFather) and `MINIMAX_API_KEY`.

## Switch-over rule

Only one reader may poll the bot. When the new version starts calling `getUpdates`, disable both Claude scheduled tasks ("AIS marketing agent - daily post" and "AIS marketing agent - Telegram buttons") at the same moment, or the two will steal each other's button presses. Start the new one from `data/state.snapshot.json` (or re-export state.json from Drive right before the switch) so the offset and post history carry over.
