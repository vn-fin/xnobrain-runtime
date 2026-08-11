# 09 · Hermes Agent — Full Use-Case Catalogue & Read-Across

> Source: [hermes-agent.nousresearch.com/docs/user-stories](https://hermes-agent.nousresearch.com/docs/user-stories),
> fetched 2026-07-24. The page lists **262 stories** across **15 categories** from
> **11 source channels**. **220** were captured in full detail here; the final ~42
> were cut by the page's content-length limit (noted at the end). Each entry below is
> a compressed-but-faithful record: title, what the agent does, and the named
> tools/models/numbers. **Sections C–E carry the analysis** — which stories are nearest
> to Twin Terminal, where Hermes and OpenClaw appear together, and what it means for
> Brain4All.

## Page structure (verbatim)

- **262 stories · 15 categories · 11 sources.**
- **Categories (with counts):** Dev Workflow (65) · Personal Assistant (44) ·
  Integrations (26) · Meta & Ecosystem (21) · Creative (19) · Business Ops (16) ·
  Cost Optimization (13) · Content Creation (11) · Research (9) · Enterprise (9) ·
  Messaging (8) · Privacy & Self-Hosted (8) · General (6) · Trading & Markets (5) ·
  Marketing (2).
- **Sources (with counts):** Discord (116) · X/Twitter (42) · GitHub (38) · Blog (20) ·
  YouTube (17) · Reddit (15) · GitHub Gist (4) · Hacker News (4) · LinkedIn (3) ·
  Podcast (2) · Product Hunt (1).

---

## A. The catalogue, by category

> Format: **#N — Title** · what it does; *tools/models/numbers* (source).

### Dev Workflow (self-improvement, memory kernels, multi-agent, tooling)

- **#3** — Local-first cognition layer; building a memory/cognition layer for Hermes (Discord).
- **#10** — Codex watches Hermes agent-to-agent workflows live; runtime monitor catches breaks, live fixes; *Codex, GPT-5.4 extra-high* (X).
- **#12** — "Converse mode"; plugin makes agent talk before executing tools (approval-first) (Discord).
- **#13** — Token profiling; dashboard finds *73% of every API call is fixed overhead* across 6 request dumps; *Hermes v0.6.0* (GitHub).
- **#25** — LaTeX→Unicode math rendering in the TUI (GitHub PR).
- **#29** — "Hadn't coded in 20 years"; vibe-coding via *Claude Code + Hermes* (Discord).
- **#31** — 200–400h memory kernel; 3-layer *L1 Hindsight / L2 Graphiti / L3 MemPalace*; project **BRAINSTACK** (Discord).
- **#41** — `hermes mcp-server` exposes 9 Hermes tools to *Claude Desktop, Cursor* (Discord).
- **#43** — Nous runs **12 Hermes instances in parallel daily** to build Hermes; *900k+ compute-seconds, 5B+ tokens*; now a top-100 GitHub repo (X, @Teknium).
- **#50** — Hooks that swap in better tools at every agent run (Discord).
- **#51** — RCA script audited **129 sessions/23 days → 112 had ≥1 approval-gate violation** (GitHub).
- **#52** — CCD multi-agent pod on M2 Ultra; *Mem0 + Qdrant*, per-agent profiles (GitHub).
- **#53** — Custom kernel: "the LLM never touches the disk"; Python compiles semantic signals into *SQLite FTS5 graph* (Discord).
- **#57** — Agent editing its own internals; worry about updates overwriting (Discord).
- **#58** — Independently built a stack, converged on Hermes (same self-improve/memory/skills design); **300 PRs in a week** (X).
- **#64** — Nightly config+DB backup to GitHub; multi-agent management (Discord).
- **#67** — Skill-audit skill that improves itself on cron (sandboxed self-improvement loop) (Discord).
- **#69** — Hermes on VPS, "phones home" over *Tailscale*; scoped tags; "yolo mode, human owns boundaries" (Discord).
- **#74** — "Day 10: it knows my codebase better than I do"; internalized prefs by 5th iteration (X).
- **#75** — 22k-line memory kernel; *temporal context graph in SQLite* with decay/promotion/supersession (Discord).
- **#78** — Local network **Kanban** so agents see task state; project **Goban** (Discord).
- **#84** — Agent auto-acts on file-change events (X).
- **#86** — **Cartographer** (memory: semantic wiring, emotional topology → temporal graph in SQLite) + **Agent IRC** (real-time chat between Hermes, Claude, Codex, OpenCode, Gemini) (Discord).
- **#88** — Multi-agent auto-build: *GPT-5.4 plans → MiniMax M2.7 codes → local Qwen 35B QA → repair → ship* (X).
- **#92** — 5 apps built & launched in a single day (LinkedIn).
- **#102** — Agent ships micro-apps via *Val Town* (Discord).
- **#104** — Long-running instance accumulates codebase knowledge (commit style, legacy API sequences) (Medium).
- **#106** — **Recall**: Hermes-native inspectable (non-black-box) memory provider (Discord).
- **#109** — Non-coder had *Codex* build a full VPN service (Xray/Wireguard, admin panel) (Discord).
- **#116** — Local *Gitea + watchtower* auto-restarts Hermes within 10 min of push (Discord).
- **#119** — Memory kernel that "compiles thoughts, not vectors"; auto contradiction resolution (Discord).
- **#126** — Hermes more stable; used to troubleshoot **OpenClaw** (Reddit).
- **#127** — Native SwiftUI Mac app (SSH to host/files); **Hermes Desktop v0.4.0** (Discord).
- **#128** — Session-compression plugin preserving work thread; **Hermes Operational Checkpoint** (Discord).
- **#136** — Persistent structured memory because compression drops constraints after ~30 turns; **Hermes Memory** (Discord).
- **#140** — Compile skills into code, invoke AI only at needed steps (model-agnostic reliability) (Discord).
- **#143** — Built-in **Kanban multi-agent**: parent posts cards → child sub-agents pull → parallel → report; "GAME CHANGING" (Reddit).
- **#149** — Vectorless RAG via *PageIndex* + tool-based reasoning (Discord).
- **#156** — Custom TUI so Hermes "feels like OpenCode"; project **Herm** (Discord).
- **#158** — **Rookery**: local llama-server process manager (Discord).
- **#160** — UI for the memory system they loved after trying *Mem0/QMD/Mempalace/Honcho*; **OpenConcho** (Discord).
- **#162** — Telegram → *Modal* serverless; *~40% faster* on research vs fresh agent; TokenMix benchmark (Medium).
- **#163** — "Every OpenClaw update breaks something — Hermes just runs" (Reddit).
- **#173** — Hermes in *NixOS + container* (Discord).
- **#178** — **Dream Auto**: idle *MCTS background reasoning*, injects insights into context (Discord).
- **#181** — Hermes as a **watchdog** over OpenClaw; "saves countless hours and credits" (X).
- **#184** — Competitor-analysis swarm ported from *Codex* in ~2h; custom memory routing (Discord).
- **#187** — "Hermes is OpenClaw set up + 1 week debug + RAG + memory + better tool calling"; *Qwen3.5-9b on 16GB VRAM, 10/10* (Reddit).
- **#190** — **STANDING.md** plugin injects standing instructions via `pre_llm_call` so the agent stops guessing (Discord).
- **#197** — `SKILL.md` as a Notion/Outlook/SharePoint tool router (Discord).
- **#198** — 3,000+ self-improvement logs on a custom NL harness; *MiniMax m2.7* (Discord).
- **#207** — **Skill Factory**: silently watches workflows → writes `SKILL.md` + `plugin.py` (GitHub).
- **#213** — 8h/day email pipeline; *DBOS + PostgreSQL + S3 + Gmail API + Claude Opus*, 3-actor (GitHub).
- **#217** — Hermes orchestrates *Claude Code/Codex over SSH*; Hermes writes prompts & reviews (Discord).
- **#218** — All llama.cpp run/optimize knowledge packaged as a skill (Discord).
- *(plus further Dev Workflow entries among the ~42 uncaptured tail).*

### Personal Assistant (proactive, memory, family, health)

- **#1** — "Every weekday 9am summarize inbox → Slack"; NL cron; writes its own skills (Blog).
- **#2** — Self-hosted Google Drive via *Nextcloud + LibreOffice* (Discord).
- **#5** — "Google me and ship a landing page to my VPS"; search → build → *SSH* deploy → text me (X).
- **#19** — Google Tasks create/update/list (GitHub).
- **#21** — Bedtime stories with consistent protagonist across sessions (GitHub).
- **#22** — Daily Obsidian journaling; testing *Kimi 2.5* (OSS models weaker at skill-triggering vs Sonnet 4.6) (Discord).
- **#33** — Raspberry Pi 5 running Hermes 24/7; memory not synced across devices (Discord).
- **#36** — Tasks across *Obsidian + Apple Calendar + Signal*, Turkish, cron (Discord).
- **#38** — "Claude (Opus 4.7) for chat, Hermes 24/7 on a mini PC for real-world stuff" (email, forms, calendar) (Discord).
- **#44** — Two-tier email: Python detects, LLM fires only when needed; *himalaya IMAP* (Discord).
- **#60** — Pi 4 home-server "central brain"; persistent memory (GitHub).
- **#62** — *Qwen3.5:4b on a 5060Ti*; Telegram assistant; 4B "snappy, alive" (Reddit).
- **#63** — Discord assistant on *GPT-5.5 / DeepSeek v4*; "life changing" (X).
- **#72** — Voice-first fitness coach learning body patterns (training→nutrition→recovery); Telegram (Discord).
- **#73** — Meal planner; **Meal Manager** plugin; weighted score *60% availability/40% recency* (Discord).
- **#82** — Apple Health + Threads + Gmail + Calendar in one CLI; "Hermes = CEO, OpenClaw = Senior Engineer" both on Obsidian (Substack).
- **#95** — 3-layer memory doctrine: durable facts / session search / skills; "save facts, not task progress" (Discord).
- **#96** — "9am check HN → DM Telegram"; MEMORY.md + USER.md, FTS5 search, NL cron (dev.to).
- **#101** — Proactive check-ins ("anything to watch this afternoon?") (GitHub).
- **#105** — "5 things Hermes does ChatGPT won't": persistent memory, runs code, acts in apps, messages first (Reddit).
- **#107** — **Obsidian vault as long-term memory backbone**; 794 upvotes (Reddit).
- **#115** — Personal assistant on *Qwen3.5 27B* (VERY good) (Reddit).
- **#122** — Semantic knowledge substrate over Obsidian/vimwiki/Hermes sessions; **Cartographer + mapsOS** (Discord).
- **#124** — Cron nudges via Discord/Signal for executive function; ~14k tokens (Discord).
- **#135** — One Hermes for a family of 3 on WhatsApp; replaced a $200 ChatGPT sub (X).
- **#147** — Hermes over iMessage on always-on Mac Studio; in group chats (GitHub).
- **#174** — Reads HackerNews → daily email summary (Discord).
- **#176** — Obsidian + home automation + server mgmt on a cheap locked-down VPS (HN).
- **#179** — PM agent runs morning/evening standups for ADHD; Manager + Paperclip sub-agents (X).
- **#180** — Memory lets user jump between projects (vs OpenClaw "one-track"); + Paperclip (Reddit).
- **#188** — iOS sensors (health/location/voice) → contextual answers (Discord).
- **#195/#205** — Health Connect / Whoop biometric data pulled in locally (Discord).
- **#202** — "Replaced everything with a single Hermes"; autoresearch + LLM-wiki second brain (X).
- *(#45, #55/#56 mapsOS, others).*

### Integrations (MCP, connectors, hardware, commerce)

- **#4** — *Hindsight Cloud* memory connector; *Vectorize.io* (LinkedIn).
- **#14** — Team agent: *SourceDev* repo index, *Tenderly MCP* onchain debug, LLM-Wiki (Discord).
- **#15** — Hermes + Browser Harness on Hostinger VPS; *claude-opus-4.7 via OpenRouter* (Gist).
- **#17** — **jMunch MCP**: *52 tools* via tree-sitter for code intelligence (GitHub).
- **#47** — **Vercel Sandbox** backend (microVMs, snapshot FS); backends now local/Docker/Modal/SSH/Daytona/Singularity/Vercel (GitHub PR).
- **#48** — Full *Feishu (Lark)* coverage (Docs/Sheets/Bitable/Calendar/Wiki/Drive/Email) (GitHub).
- **#93** — *Firecrawl* scrape/search/browse (LinkedIn).
- **#97** — **Onchain identity + proof-of-work** attestations via *Ethereum Attestation Service* on Base mainnet (Discord).
- **#103** — *Hunter.io* email lookup via *Composio MCP* for sales (GitHub).
- **#108** — `hermes mcp serve`: "fat agent → thin tool provider"; exposes 15+ platforms, FTS5, 73-skill surface (Gist).
- **#117** — AdGuard Home plugin (Discord).
- **#118** — Themed browser **Webchat** UI on MEMORY.md + USER.md (GitHub).
- **#132** — Watches homelab validators (*0G, FortyTwo*), pings Telegram on state change; ex-OpenClaw (Discord).
- **#137** — Cross-agent memory across *Hermes + Claude Code + Cursor*; BM25 + vector + KG (GitHub).
- **#138** — Remote-start car via *OnStar* skill (Discord).
- **#139** — Desktop computer-use module (noVNC, screenshots, mouse/keyboard) (GitHub).
- **#142** — *JMAP* email for Fastmail (GitHub).
- **#151** — Discord-read plugin (missed from OpenClaw) (Discord).
- **#159** — Bundled many API keys into single endpoints (finance via one call) (Discord).
- **#170** — *BoltAI v2* gateway plugin (markdown + slash commands) (Discord).
- **#186/#209** — **Home Assistant** add-on; "zero to agent in under 5 min" (Discord/X).
- **#189** — **agentbox.id**: agent-optimized email service (Discord).
- **#200** — *M5 Cardputer* embedded device via API (OTA, TTS/STT) (Discord).
- **#204** — **Merxex**: agent-to-agent **commerce**/monetization layer (buy/sell services) (GitHub).
- **#214** — Agent's own inbox via *AgentMail MCP* (no SMTP/OAuth) (X).
- **#219** — Hermes in *Zed* via **ACP Registry** (auto discovery/install) (GitHub).

### Meta & Ecosystem (dashboards, installers, ecosystem maps, hosting)

- **#20** — **hermes-for-win**: one-click Windows installer, auto-start (GitHub).
- **#24** — Podcast: "Hermes has won" — self-improving skills, 3-layer memory (Spotify).
- **#28** — TUI dashboard watching the agent think; **Hermes HUD** (Discord).
- **#49** — Show HN independent install guide (macOS/Linux/WSL2/Termux) (HN).
- **#80** — Browser dashboard: PTY terminal, file editor, gateway control, token analytics (Discord).
- **#94** — Every tool call → per-profile SQLite + *5 Grafana dashboards* (Discord).
- **#111** — "One month with Hermes: don't build the whole machine on day one" (Reddit).
- **#145** — "Switched from OpenClaw to Hermes, not looking back" (X).
- **#146** — **awesome-hermes-agent**; tied to **agentskills.io** standard (GitHub).
- **#153** — 4 custom skins for HermelinChat GUI (Discord).
- **#164** — Product Hunt: competitor (Clawdi) calls it "the best self-improving agent we've used" (Product Hunt).
- **#167** — Native Windows app wrapper (Reddit).
- **#171** — macOS control center for local models on 2 machines (Discord).
- **#182** — **H-OPS**: operator dashboard for multi-agent on Hermes Kanban (Discord).
- **#183** — **hermesatlas.com**: scraped whole ecosystem, star-rated by category (X).
- **#192** — Mini-documentary with hackathon finalists + Nous co-founder (Discord).
- **#196** — 4 agents (PM/Dev/Ops/Content) 24/7 on 32GB Ubuntu; *5 MCP servers, 34 tools*, daily auto-distillation (Discord).
- **#201** — **Hermify**: managed hosting (bring API key + Telegram bot) (Reddit).
- **#211** — Shadow-to-live **migration path from OpenClaw** (GitHub).

### Business Ops

- **#42** — Roofing lead-gen/CRM app (Discord).
- **#59** — 24/7 assistant on *Supabase CRM*; agent proposed a "Supabase MCP scripts" skill itself (YouTube).
- **#100** — Triages & works tickets in *Plane.so*; documents to Obsidian; + Claude Code (Discord).
- **#103** — Hunter.io sales outreach (see Integrations).
- **#113** — Create/edit *Google Slides* decks (GitHub).
- **#125** — "Day 297 streak: **$100K of client work automated**"; 900k+ compute-sec, 5B+ tokens (X).
- **#134** — **Hermes as Chief of Staff**: main agent w/ cross-project memory + per-project sub-agents (one per Slack channel); daily WhatsApp report (Discord).
- **#168** — Task-centric memory for a printing factory; auto-categorize (Printing/Stocks), compress done tasks to cards (GitHub).
- **#184** — Competitor-analysis swarm (see Dev Workflow).
- **#194** — Auto-transcribe Meet, control from Teams, local models for client data (Substack).

### Cost Optimization

- **#27** — Switch Hermes/OpenClaw with free models on *primeclaws.com* (Reddit).
- **#30** — Replaced Perplexity (~$10/few days) with **Gigaxity** (7 MCPs + SearXNG) (Discord).
- **#61** — Multi-agent, weeks continuous, Telegram; "what I use it for & how I keep it cheap" (X).
- **#70** — **ZeroID**: *RFC 8693 token exchange* for sub-agent scope delegation & context cost (Discord).
- **#99** — **90% token cut** (~$130/5d → ~$10/5d); Android via *Termux + OpenRouter*; "customization is a trap; output is the skill" (Podcast).
- **#112** — Under **$20/mo** (Minimax M2.7 VPS) vs OpenClaw Mac-Mini-M4 + Opus 4.6 ~$80–150/mo (Medium).
- **#114** — Free GPT-4.1 via Copilot Pro ($10/mo); Hermes delegates coding to *OpenCode* (Discord).
- **#148** — $10/mo Hetzner VPS; *Claude Opus via OpenRouter* (YouTube).
- **#165** — Smart-routing tiers (*Gemini 3.1 Flash Lite* mechanical / *Sonnet* delicate / *Minimax* low-overhead); saved ~10h + $40 (Reddit).
- **#177** — Multi-agent on *Ollama* to cut cost (Discord).
- **#185** — **RTK** integration rewrites terminal commands; **60–90% context-token cut** (Discord).

### Content Creation / Marketing

- **#6** — Turkish locale skill pack (TRY data, Turkish news, daily PNG cards, Telegram cron); zero API keys (Discord).
- **#8** — Skill pack on *Meta CLI/MCP* (Marketing) (Discord).
- **#16** — Weekly cron: top-3 trending AI tools → makes a reusable skill (YouTube).
- **#40** — **UGC ad studio**: URL → scrape → *Meta Ads Library + TikTok Creative Center* hooks → brief in *~4 min*, zero prompt-eng; *Higgsfield* (X).
- **#77** — X roast-poster without a $100 API sub (Discord).
- **#123** — Writes in the user's voice (reads their articles first); Mac Mini running OpenClaw + Hermes (X).
- **#129** — Cron triages tech news into Discord channels by urgency, 3×/day (X).
- **#141** — Tweets in the creator's voice from past scripts; recalls preferred emojis in a new session (YouTube).
- **#155** — LinkedIn posts that remember the user's style (YouTube).

### Research

- **#30** — Custom research stack (see Cost).
- **#65** — Daily research brief → Discord/Slack/Notion/Obsidian/email; tracks ignored items, self-improves (X).
- **#66** — **Hermes-lab**: autonomous experiment bookkeeper (Karpathy/Sakana/AIDE-inspired) (Discord).
- **#76** — Ported the Python weather stack (MetPy/Herbie/cfgrib/WRF) to Rust for plugins (Discord).
- **#79** — Self-improving **LLM Wiki second brain** (Karpathy pattern); public site (Medium).
- **#206** — **AI-assisted drug discovery for Africa** (pharmacy undergrad); *ChEMBL, AlphaFold, OpenFDA, QSAR* (Discord).

### Enterprise

- **#32** — Daily cybersec+AI briefing on a **local k8s cluster** (Discord).
- **#37** — Native **Vertex AI** provider for GCP-standard orgs (GitHub).
- **#54** — **EU AI Act compliance via Ombre**: tamper-proof audit, prompt-injection blocking, memory encryption, hallucination detection, cost tracking, compliance exports (GitHub).
- **#90** — Azure-compliant prompt patch to avoid content-filter trips (Gist).
- **#130** — CLI/gateway-first: **13 messaging platforms under one process** (Substack).
- **#175** — AWS VPS + Google Workspace automation; setup non-trivial (Discord).
- **#193** — "95% of AI users see no results" VC deep-dive on Hermes swarms/experiment loops (X).
- **#208** — **Kubernetes pod-hop handoff** across restarts on shared PVC (GitHub).

### Trading & Markets

- **#7** — Self-learning weather-trading bot: scans every 60 min, compares 3 forecasts, buys undervalued buckets; **$100 → $216 in 48h** (X).
- **#81** — Polymarket: reads 4 layers in parallel (order book, on-chain addresses, news-lag, positions); *Polymarket module + News Skill* (X).

### Messaging

- **#23** — **QQ Bot** adapter for China (822 lines; 95M+ users) (GitHub).
- **#26** — DM-based **approval gate** for kid-facing Discord bots (GitHub).
- **#34** — **LINE** integration ask (95M+ MAU Japan) (GitHub).
- **#157** — Native Android client **Hermes Relay** (streaming, slash cmds, tool viz) (Discord).
- **#191/#203/#210** — Remote phone control / home-server + Telegram / web-proxy session hand-off to mobile (Discord).

### Privacy & Self-Hosted

- **#9** — Shared local *SearXNG* container across agents (Discord).
- **#85** — **Legal work on an edge GPU, 4B Gemma, no cloud APIs**; "self-hosting the main loop is non-negotiable" (GitHub).
- **#91** — "Sandbox it — don't give it free reign" (HN).
- **#131** — *Tailscale serve* — secure remote access, no exposed ports (GitHub).
- **#154** — Independent security eval: 5 defensive patterns (OSV malware check for MCP packages, credential stripping) (Gist).
- **#212** — Skill that hardens the agent against common LLM threats (Discord).

### Creative

- **#35** TouchDesigner generative visuals · **#39** B1 droid skin · **#46** X→NotebookLM podcast workflow (agent-designed) · **#71** chess blunder finder (blunder-lens.com) · **#87** agent "dreams" nightly, 5 REM cycles 23:00–06:00, **~$0.014/night on Haiku** · **#89** shadcn finance dashboard + Manim explainers · **#98** Matrix skin · **#110** auto-play Minecraft skill (20+ min thinking) · **#120** **Hermes Inc.** Telegram startup-sim (AI teammates argue/remember/evolve) · **#121** personal web-dev style as a skill · **#133** speech-to-speech + generated ambient music · **#150** agent tone examples co-written with a sibling (agent "Reina") · **#166** long voice-call timeout plugin · **#169** twice-daily Tidal curation · **#192** documentary · **#215** spare-laptop Hermes autonomously builds a **RenPy visual novel (10 images) in ~10 min** via LM Studio + ComfyUI · **#216** browser translate/summarize extension (Hermes-4-70B).

### General

- **#11** Voice-from-terminal for accessibility (*Whisper.cpp*) · **#68** "AI employee for my hardest tasks" (Hermes + ChatGPT 5.5) · **#144** local community agent on a 16GB Mac mini · **#152** teaching a Linux user group to build agents · **#161** **blind-since-birth user built an NVDA screen-reader translator addon** · **#199** Spanish Hermes guide built with Hermes.

*(#220 and ~41 further stories in the page's tail were cut by the fetch content-limit;
the captured 220 already cover all 15 categories and every named integration pattern.)*

---

## B. The hard numbers, pulled out

| Metric | Story |
|---|---|
| **$100 → $216 in 48h** (weather-trading bot) | #7 |
| **$100K of client work automated** (day-297 streak) | #125 |
| **90% token-spend cut** ($130/5d → $10/5d, Android/Termux) | #99 |
| **60–90% context-token cut** (RTK) | #185 |
| **73% of each API call is fixed overhead** (measured) | #13 |
| **$0.014/night** dream cycles on Haiku | #87 |
| **Under $20/mo** full setup (Minimax VPS) vs OpenClaw $80–150/mo | #112 |
| **~40% faster** research vs a fresh agent | #162 |
| **112/129 sessions** violated the approval gate (audit) | #51 |
| **12 parallel Hermes instances/day; 5B+ tokens; top-100 GitHub repo** | #43 |
| **794 upvotes** for the Obsidian-as-memory pattern | #107 |
| **52 tools** (jMunch MCP) · **73-skill** surface (mcp serve) | #17, #108 |

---

## C. Stories NEAREST to the Twin Terminal thesis (the read-across)

These are the ones to study — each is a fragment of an engine you plan to own.
See [02-vision-twin-terminal.md](02-vision-twin-terminal.md) for the engine numbers.

**Encoding a named person's craft / voice (Engine 02 + 06 — the "twin" itself).**
The single most on-thesis cluster: Hermes users are *already cloning individual
judgment and voice.*
- **#123** writes in the user's voice (reads their articles first); **#141/#155**
  learn and *remember* a creator's style + emojis across sessions; **#150** co-writes
  the agent's tone with a sibling. → This is a **consumer-grade expert twin** with **no
  fidelity certification**. Your gate is exactly the missing layer.
- **#82** "Hermes = CEO, OpenClaw = Senior Engineer" — role-specialized twins already
  collaborating.

**Capturing scarce/operational expertise (your supply thesis).**
- **#168** printing-factory task memory; **#14** team protocol/onchain knowledge;
  **#206** drug-discovery workflows; **#218** "all my model-running knowledge as a
  skill." → Same instinct as **Cloneable** ([05-startup-landscape.md](05-startup-landscape.md)):
  turn tacit craft into reusable agents. None certify fidelity.

**Multi-agent councils / orchestration (Engine 04).**
- **#134** Chief-of-Staff + per-project sub-agents; **#143/#78** Kanban parent→child
  fan-out; **#88** plan→code→QA→ship pipeline; **#181** watchdog agent; **#196** 4-agent
  org with auto-distillation. → Your "cross-org twin councils / disagreement maps" have
  working single-org precedents here.

**Verification, approval, audit, drift (Engine 07 — the moat's neighbors).**
- **#12/#26** approval gates; **#51** *session audit that measured 112/129 gate
  violations*; **#54** Ombre EU-AI-Act compliance (tamper-proof audit, hallucination
  detection); **#190** STANDING.md so the agent stops guessing; **#154** security eval.
  → The ecosystem is *reaching for trust/audit primitives* but **nobody measures
  fidelity-to-a-named-expert**. This is the clearest confirmation that Engine 07 is
  open whitespace **even inside the Hermes community.**

**Memory as a durable, inspectable asset (Engine 02).**
- **#31/#53/#75/#86/#106/#119/#136/#160** — an entire cottage industry of memory
  kernels (SQLite graphs, temporal decay, contradiction resolution, "inspectable, not
  black-box"). → Your "craft ledger travels with the twin" is *the same need*, one
  level up. You can likely **adopt/partner** rather than build memory from scratch.

**Commerce / identity / attestation (Engines 05 & 09).**
- **#204 Merxex** = agent-to-agent **commerce** layer; **#97** = **onchain identity +
  proof-of-work attestation** (Ethereum Attestation Service). → Primitive versions of
  your identity + royalty engines already exist to build on or learn from. Attestation
  on-chain is one candidate mechanism for tamper-evident provenance/badges.

**Skill auto-generation & a skill standard (Engine 06 + the registry).**
- **#207 Skill Factory** and **#67** self-improving skill-audit; **#146 agentskills.io**
  standard + **#183 Hermes Atlas** registry/ratings. → The "workshop that fills the
  shelf" and a **skills standard + rated registry already exist** in the Hermes world.
  Your registry should **interoperate with agentskills.io**, not reinvent it — and add
  the one thing Atlas's star-ratings lack: *measured fidelity + expiry.*

## D. Hermes and OpenClaw deployed together

OpenClaw appears constantly as Hermes's reference point — often **running side by
side**:
- **#82** Hermes(CEO)+OpenClaw(Sr Eng) on one Obsidian vault; **#123** both on one Mac
  Mini; **#181** Hermes as a *watchdog over OpenClaw*; **#126** Hermes troubleshoots
  OpenClaw; **#132/#151/#180** users migrating from OpenClaw but porting its features.
- **Migration/switching:** **#27** primeclaws.com switches between them; **#145**
  "switched, not looking back"; **#187** "Hermes is OpenClaw + 1 week debug + RAG +
  memory"; **#163** "every OpenClaw update breaks something"; **#211** shadow-to-live
  migration tool; **#211/#151** feature-parity ports.

**Implication:** the two ecosystems are **interoperable and adjacent**, and users
already run multi-runtime setups. A platform that is **runtime-agnostic at the twin
layer** (certify/host twins regardless of whether the underlying runtime is Hermes or
OpenClaw) is a credible positioning — it rides the fact that users already mix them.
The startups leveraging *both* today are mostly **individual power users and small
tools** (primeclaws.com, watchdog setups, migration utilities), not funded companies —
i.e. the "twin-over-many-runtimes" company slot is **still open.**

## E. What this catalogue means for Brain4All

1. **Demand is proven and broad.** 262 real, sourced stories across 15 domains, most
   from individuals and small teams — the *self-hosted personal/expert agent* market is
   live, not speculative.
2. **Every commodity engine is already saturated** by the community (memory kernels,
   dashboards, connectors, routing, sandboxes). **Do not build these** — adopt,
   partner, or ride the standard (agentskills.io, Atlas, Merxex, EAS). Confirms
   [03](03-core-tech-and-competitors.md)/[08](08-roadmap.md): rent the commodity.
3. **The moat is confirmed empty from the inside.** The community is *actively building
   audit, approval, compliance, and trust primitives* (#51, #54, #154, #190) — but
   **not one measures whether a twin faithfully reproduces a named human.** Engine 07
   is whitespace even among the people closest to the tech.
4. **Cloning individual voice/judgment is already the killer use case** (#123, #141,
   #155, #150) — it just lacks certification, provenance, and royalties. That is
   precisely the Twin Terminal wedge, and it means **you're extending a proven behavior,
   not inventing demand.**
5. **Interoperate, don't reinvent the registry.** agentskills.io + Hermes Atlas are the
   incumbents of the "shelf." Your differentiation is **measured fidelity + expiry +
   royalty lineage** layered on top of (or bridging) that standard.
6. **Nous already monetizes hosting (Hermify).** Reinforces [03](03-core-tech-and-competitors.md):
   don't compete on hosting; compete on certification.
