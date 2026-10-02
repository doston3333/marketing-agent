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
| `prompts/*.md` | The routine prompts (marketing: daily-post, telegram-buttons; tenders: tender-review). |
| `assets/Montserrat.ttf` | Brand font. |
| `tender.py`, `lib/tenders.py`, `lib/browser.py` | The tender agent (below). |

# Tender agent

A second, separate agent in the same repo. Every working day it opens the tender sites, signs in where it has an account, collects the lots it has not seen before, and Claude reviews them against AI Station's profile and sends one Uzbek digest to Telegram: what to bid on, what to watch, why, and the next step.

```
weekdays 09:20 ─┬─ inbox    the team's replies to tender digests (set aside by the marketing agent's bot reader)
                ├─ scan     14 sites in a headless browser (+ World Bank API) -> new lots, keyword pre-score
                ├─ profile  what AI Station bids on + team feedback + recently recommended
                ├─ Claude   triages every new title, opens the promising lots (tabs, documents, scanned PDFs),
                │           judges bid / watch / skip, writes the review (Uzbek)
                ├─ digest   Telegram message to the tender recipients
                └─ save     commits only the tender files in data/
```

| Site | How it is read | Login |
|---|---|---|
| UZEX e-Tender (etender.uzex.uz) | the site's own lot feed (best-offer + tanlov, up to 500 + all) | E-IMZO only; everything is public |
| UZEX Xarid (xarid.uzex.uz) | competitions feed (newest 300); auctions (goods) left out | E-IMZO only |
| eBirja (ebirja.uz) | public API (auctions + requests for offers) | E-IMZO only |
| UzbekistanTenders, GlobalTenders, TendersInfo, BidDetail | listing pages filtered to Uzbekistan | optional, paid accounts show full notices |
| TenderWeek | latest tenders page | optional (free account) |
| World Bank | official procurement API, Uzbekistan | none |
| ADB | tender search for "Uzbekistan" | none |
| UNGM | notices with beneficiary country Uzbekistan | optional |
| EBRD | notices searched for Uzbekistan (most now live on ECEPP) | none |
| IsDB, OSCE | open tender lists | none |
| TendersOnTime, DevelopmentAid | **off and ignored**: Cloudflare blocks headless browsers from cloud IPs | - |

The site list, URLs and extraction rules are data, not code: `data/tender_sites.json`. What counts as a fit: `data/tender_profile.md` (edit it in plain words) and `data/tender_keywords.json` (pre-filter). Seen lots and verdicts: `data/tenders.json`.

## Setup

1. **Telegram**: nothing to set up. The digest goes through the marketing bot (@marketingagent67_bot) to the marketing leads. That bot is read only by the marketing agent: when a lead replies to a tender digest, or writes a message starting with "tender" ("tender: qurilish kerak emas"), it does not treat it as a post edit but parks it in `state.json` → `tender_inbox` and answers "📑 Tender agentiga yetkazildi"; `tender.py inbox` picks those up on the next run. (Optional: a separate bot via `TENDER_BOT_TOKEN`, which this agent then reads itself, for sending the digest to other people or a group; `TENDER_CHAT_IDS=id1,id2` overrides the recipients.)
2. **Site accounts (optional)**: `TENDER_<SITE>_USER` and `TENDER_<SITE>_PASS`, e.g. `TENDER_UNGM_USER`, `TENDER_GLOBALTENDERS_PASS` (site ids in `python3 tender.py sites`). Check one with `python3 tender.py login ungm`. UZEX and eBirja need no account to read.
3. **Network access**: the environment must reach the tender sites (`*.uzex.uz`, `ebirja.uz`, `*.ebirja.uz`, `uzbekistantenders.com`, `globaltenders.com`, `tenderweek.com`, `tendersinfo.com`, `biddetail.com`, `search.worldbank.org`, `projects.worldbank.org`, `adb.org`, `*.searchstax.com`, `ungm.org`, `ebrd.com`, `isdb.org`, `procurement.osce.org`) plus `api.telegram.org` and `api.github.com`.
4. **Routine**: a Claude Code cloud routine in this repo with the prompt `prompts/tender-review.md`, schedule `CRON_TZ=Asia/Tashkent 20 9 * * 1-5`. It installs `requirements-tender.txt` (Playwright, document readers) itself; Chromium comes with the cloud image, and `lib/browser.py` imports the environment's CA bundle into Chromium's store so pages load behind the egress proxy.
5. Check with `python3 tender.py check`.

## Commands

```
python3 tender.py check | sites | login SITE
python3 tender.py scan [--site ID ...]           # collect new lots
python3 tender.py new                            # unreviewed lots for Claude
python3 tender.py open KEY|URL [--click TAB]     # read one lot (tabs, documents, download buttons)
python3 tender.py doc URL | doc KEY --click "Faylni yuklab olish"   # document text (pdf/docx/xlsx)
python3 tender.py profile | review FILE | digest FILE [--dry-run] | say HTML | inbox | save
```

The two agents never touch each other's state: `tender.py save` commits only the four tender files, and `agent.py save` / the Telegram memory backup skip them.
