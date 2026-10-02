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
daily 08:50 ─┬─ start      Telegram: register leads, store lead ideas (photos / forwards / "g‘oya: ...")
             ├─ metrics    @aistationuz archive with views -> what performs (performance.json)
             ├─ collect    ~700 items: 13 Telegram channels, 14 RSS feeds, Google News (EN/RU), Hacker News
             │             -> clustered + scored story bank (story_bank.json, kept 7 days)
             ├─ knowledge  guide + team rules + measured house style + over-used phrases + leads' edits
             ├─ today      weekly plan slot (content_plan.json) + opportunities + lead ideas + top stories
             ├─ Claude     picks, deep-reads, verifies, writes (examples by topic+views, 3 hooks, judge, editor pass)
             ├─ render     image candidates (MiniMax concept / real photo tinted to brand / brand card) -> Claude looks
             └─ send       package (+ carousel album + LinkedIn PDF) to the leads; commits data/ back to git
hourly :07 ── buttons ✅ 🎨 ✍️ 💡 📢, reply-edits (learned as rules), ideas
```

- **Claude does the thinking**: choosing the story, judging candidates, writing, judging its own drafts, looking at images.
- **The code does the mechanics** (`python3 agent.py -h`): collection, clustering, scoring, archive, style fingerprint, rendering, Telegram.
- **Learning loop**: leads' edits are stored as before/after pairs and distilled into rules (confirmed after 2 edits or when stated as a rule); rejected ideas are remembered; channel views feed `performance.json`; on Mondays `style` re-measures the house style and the phrases the agent over-uses.
- **Publishing**: off by default. `python3 agent.py settings publish_telegram on` adds a "📢 Kanalga joylash" button after approval (the bot must be an admin of @aistationuz). Instagram/LinkedIn are still posted by the leads.

| Data file | What it is |
|---|---|
| `data/state.json` | Leads, posts, history, rules learned, edits, rejections, settings |
| `data/story_bank.json` | Scored story clusters from all sources |
| `data/archive.json` | Every public @aistationuz post with views |
| `data/performance.json` | Views vs channel median by post kind / series / author |
| `data/style.json`, `data/slop.json` | Measured house style; phrases the agent over-uses |
| `data/content_plan.json` | Pillars, weekly series, priority rules |
| `data/ideas.json` | Idea bank: lead ideas + evergreen ideas |
| `data/knowledge.md` | Voice guide |

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
| `lib/agent_lib.py` | Engine: state, Telegram, MiniMax, Pillow brand renderer, lint, knowledge, publishing. |
| `lib/sources.py` | Source collectors, clustering, scoring, story bank. |
| `lib/voice.py` | Channel archive, example retrieval, style fingerprint, over-used phrases. |
| `lib/visuals.py` | Image types (photo / card / MiniMax candidates), carousels, LinkedIn PDF. |
| `data/state.json` | Live state (committed by every run). `state.snapshot.json` is the Oct 1 export it started from. |
| `data/knowledge.md` | Voice guide + content rules. |
| `prompts/*.md` | The two routine prompts. |
| `assets/Montserrat.ttf` | Brand font. |
