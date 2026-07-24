# 09 · Hermes Agent — Danh mục use-case đầy đủ & Đối chiếu

> Nguồn: [hermes-agent.nousresearch.com/docs/user-stories](https://hermes-agent.nousresearch.com/docs/user-stories),
> lấy về 2026-07-24. Trang này liệt kê **262 story** thuộc **15 danh mục** từ
> **11 kênh nguồn**. **220** story được ghi lại đầy đủ chi tiết ở đây; khoảng ~42 story
> cuối bị cắt bởi giới hạn độ dài nội dung của trang (được ghi chú ở cuối). Mỗi mục bên
> dưới là một bản ghi cô đọng nhưng trung thực: tiêu đề, agent làm gì, và các
> công cụ/mô hình/con số được nêu tên. **Các mục C–E chứa phần phân tích** — những story
> nào gần nhất với Twin Terminal, nơi Hermes và OpenClaw xuất hiện cùng nhau, và điều đó
> có ý nghĩa gì với Brain4All.

## Cấu trúc trang (nguyên văn)

- **262 story · 15 danh mục · 11 nguồn.**
- **Danh mục (kèm số lượng):** Dev Workflow (65) · Personal Assistant (44) ·
  Integrations (26) · Meta & Ecosystem (21) · Creative (19) · Business Ops (16) ·
  Cost Optimization (13) · Content Creation (11) · Research (9) · Enterprise (9) ·
  Messaging (8) · Privacy & Self-Hosted (8) · General (6) · Trading & Markets (5) ·
  Marketing (2).
- **Nguồn (kèm số lượng):** Discord (116) · X/Twitter (42) · GitHub (38) · Blog (20) ·
  YouTube (17) · Reddit (15) · GitHub Gist (4) · Hacker News (4) · LinkedIn (3) ·
  Podcast (2) · Product Hunt (1).

---

## A. Danh mục, theo từng nhóm

> Ghi chú: danh mục 220 story dưới đây được giữ nguyên tiếng Anh theo nguồn; phần phân tích (mục C–E) đã được dịch đầy đủ.

> Định dạng: **#N — Tiêu đề** · nội dung; *công cụ/mô hình/con số* (nguồn).

### Dev Workflow — Quy trình phát triển (tự cải thiện, memory kernel, đa agent, tooling)

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

### Personal Assistant — Trợ lý cá nhân (chủ động, bộ nhớ, gia đình, sức khỏe)

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

### Integrations — Tích hợp (MCP, connector, phần cứng, thương mại)

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

### Meta & Ecosystem — Meta & Hệ sinh thái (dashboard, installer, bản đồ hệ sinh thái, hosting)

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

### Business Ops — Vận hành kinh doanh

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

### Cost Optimization — Tối ưu chi phí

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

### Content Creation / Marketing — Sáng tạo nội dung / Marketing

- **#6** — Turkish locale skill pack (TRY data, Turkish news, daily PNG cards, Telegram cron); zero API keys (Discord).
- **#8** — Skill pack on *Meta CLI/MCP* (Marketing) (Discord).
- **#16** — Weekly cron: top-3 trending AI tools → makes a reusable skill (YouTube).
- **#40** — **UGC ad studio**: URL → scrape → *Meta Ads Library + TikTok Creative Center* hooks → brief in *~4 min*, zero prompt-eng; *Higgsfield* (X).
- **#77** — X roast-poster without a $100 API sub (Discord).
- **#123** — Writes in the user's voice (reads their articles first); Mac Mini running OpenClaw + Hermes (X).
- **#129** — Cron triages tech news into Discord channels by urgency, 3×/day (X).
- **#141** — Tweets in the creator's voice from past scripts; recalls preferred emojis in a new session (YouTube).
- **#155** — LinkedIn posts that remember the user's style (YouTube).

### Research — Nghiên cứu

- **#30** — Custom research stack (see Cost).
- **#65** — Daily research brief → Discord/Slack/Notion/Obsidian/email; tracks ignored items, self-improves (X).
- **#66** — **Hermes-lab**: autonomous experiment bookkeeper (Karpathy/Sakana/AIDE-inspired) (Discord).
- **#76** — Ported the Python weather stack (MetPy/Herbie/cfgrib/WRF) to Rust for plugins (Discord).
- **#79** — Self-improving **LLM Wiki second brain** (Karpathy pattern); public site (Medium).
- **#206** — **AI-assisted drug discovery for Africa** (pharmacy undergrad); *ChEMBL, AlphaFold, OpenFDA, QSAR* (Discord).

### Enterprise — Doanh nghiệp

- **#32** — Daily cybersec+AI briefing on a **local k8s cluster** (Discord).
- **#37** — Native **Vertex AI** provider for GCP-standard orgs (GitHub).
- **#54** — **EU AI Act compliance via Ombre**: tamper-proof audit, prompt-injection blocking, memory encryption, hallucination detection, cost tracking, compliance exports (GitHub).
- **#90** — Azure-compliant prompt patch to avoid content-filter trips (Gist).
- **#130** — CLI/gateway-first: **13 messaging platforms under one process** (Substack).
- **#175** — AWS VPS + Google Workspace automation; setup non-trivial (Discord).
- **#193** — "95% of AI users see no results" VC deep-dive on Hermes swarms/experiment loops (X).
- **#208** — **Kubernetes pod-hop handoff** across restarts on shared PVC (GitHub).

### Trading & Markets — Giao dịch & Thị trường

- **#7** — Self-learning weather-trading bot: scans every 60 min, compares 3 forecasts, buys undervalued buckets; **$100 → $216 in 48h** (X).
- **#81** — Polymarket: reads 4 layers in parallel (order book, on-chain addresses, news-lag, positions); *Polymarket module + News Skill* (X).

### Messaging — Nhắn tin

- **#23** — **QQ Bot** adapter for China (822 lines; 95M+ users) (GitHub).
- **#26** — DM-based **approval gate** for kid-facing Discord bots (GitHub).
- **#34** — **LINE** integration ask (95M+ MAU Japan) (GitHub).
- **#157** — Native Android client **Hermes Relay** (streaming, slash cmds, tool viz) (Discord).
- **#191/#203/#210** — Remote phone control / home-server + Telegram / web-proxy session hand-off to mobile (Discord).

### Privacy & Self-Hosted — Riêng tư & Tự lưu trữ

- **#9** — Shared local *SearXNG* container across agents (Discord).
- **#85** — **Legal work on an edge GPU, 4B Gemma, no cloud APIs**; "self-hosting the main loop is non-negotiable" (GitHub).
- **#91** — "Sandbox it — don't give it free reign" (HN).
- **#131** — *Tailscale serve* — secure remote access, no exposed ports (GitHub).
- **#154** — Independent security eval: 5 defensive patterns (OSV malware check for MCP packages, credential stripping) (Gist).
- **#212** — Skill that hardens the agent against common LLM threats (Discord).

### Creative — Sáng tạo

- **#35** TouchDesigner generative visuals · **#39** B1 droid skin · **#46** X→NotebookLM podcast workflow (agent-designed) · **#71** chess blunder finder (blunder-lens.com) · **#87** agent "dreams" nightly, 5 REM cycles 23:00–06:00, **~$0.014/night on Haiku** · **#89** shadcn finance dashboard + Manim explainers · **#98** Matrix skin · **#110** auto-play Minecraft skill (20+ min thinking) · **#120** **Hermes Inc.** Telegram startup-sim (AI teammates argue/remember/evolve) · **#121** personal web-dev style as a skill · **#133** speech-to-speech + generated ambient music · **#150** agent tone examples co-written with a sibling (agent "Reina") · **#166** long voice-call timeout plugin · **#169** twice-daily Tidal curation · **#192** documentary · **#215** spare-laptop Hermes autonomously builds a **RenPy visual novel (10 images) in ~10 min** via LM Studio + ComfyUI · **#216** browser translate/summarize extension (Hermes-4-70B).

### General — Tổng quát

- **#11** Voice-from-terminal for accessibility (*Whisper.cpp*) · **#68** "AI employee for my hardest tasks" (Hermes + ChatGPT 5.5) · **#144** local community agent on a 16GB Mac mini · **#152** teaching a Linux user group to build agents · **#161** **blind-since-birth user built an NVDA screen-reader translator addon** · **#199** Spanish Hermes guide built with Hermes.

*(#220 và khoảng ~41 story khác ở phần đuôi của trang đã bị cắt bởi giới hạn độ dài nội dung khi lấy về;
220 story đã ghi lại vẫn bao phủ toàn bộ 15 danh mục và mọi mẫu hình tích hợp được nêu tên.)*

---

## B. Các con số cứng, được rút ra

| Chỉ số | Story |
|---|---|
| **$100 → $216 trong 48h** (bot giao dịch theo thời tiết) | #7 |
| **$100K công việc khách hàng được tự động hóa** (chuỗi ngày thứ 297) | #125 |
| **Cắt 90% chi phí token** ($130/5 ngày → $10/5 ngày, Android/Termux) | #99 |
| **Cắt 60–90% token ngữ cảnh** (RTK) | #185 |
| **73% mỗi lệnh gọi API là chi phí cố định** (đo được) | #13 |
| **$0.014/đêm** cho các chu kỳ "dream" trên Haiku | #87 |
| **Dưới $20/tháng** cho toàn bộ thiết lập (Minimax VPS) so với OpenClaw $80–150/tháng | #112 |
| **Nhanh hơn ~40%** khi nghiên cứu so với một agent mới toanh | #162 |
| **112/129 phiên** vi phạm cổng phê duyệt (kiểm toán) | #51 |
| **12 phiên Hermes song song/ngày; 5B+ token; repo GitHub top-100** | #43 |
| **794 upvote** cho mẫu hình Obsidian-làm-bộ-nhớ | #107 |
| **52 công cụ** (jMunch MCP) · bề mặt **73 skill** (mcp serve) | #17, #108 |

---

## C. Những story GẦN NHẤT với luận điểm Twin Terminal (phần đối chiếu)

Đây là những story cần nghiên cứu — mỗi story là một mảnh của cỗ máy mà bạn dự định sở hữu.
Xem tài liệu tầm nhìn Twin Terminal (02) để biết các con số của engine.

**Mã hóa nghề/giọng của một người cụ thể được nêu tên (Engine 02 + 06 — chính là "twin").**
Đây là cụm đúng-luận-điểm nhất: người dùng Hermes *đã và đang nhân bản phán đoán và
giọng của từng cá nhân.*
- **#123** viết theo giọng của người dùng (đọc bài viết của họ trước); **#141/#155**
  học và *ghi nhớ* phong cách + emoji của một nhà sáng tạo qua nhiều phiên; **#150** cùng
  viết giọng của agent với một người thân. → Đây là một **twin chuyên gia cấp tiêu dùng**
  với **không có kiểm định độ trung thành**. Cổng của bạn chính là lớp còn thiếu đó.
- **#82** "Hermes = CEO, OpenClaw = Kỹ sư cấp cao" — các twin chuyên biệt theo vai trò đã
  đang cộng tác với nhau.

**Nắm bắt chuyên môn khan hiếm/vận hành (luận điểm về nguồn cung của bạn).**
- **#168** bộ nhớ tác vụ của nhà máy in; **#14** kiến thức giao thức nhóm/onchain;
  **#206** quy trình khám phá thuốc; **#218** "toàn bộ kiến thức chạy mô hình của tôi
  đóng gói thành một skill." → Cùng bản năng với **Cloneable** (xem tài liệu bối cảnh
  startup, 05): biến nghề ngầm định thành các agent tái sử dụng được. Không có story nào
  kiểm định độ trung thành.

**Hội đồng đa agent / điều phối (Engine 04).**
- **#134** Chief-of-Staff + các sub-agent theo dự án; **#143/#78** Kanban fan-out cha→con;
  **#88** pipeline lập kế hoạch→code→QA→ship; **#181** agent watchdog; **#196** tổ chức 4
  agent với auto-distillation. → "Các hội đồng twin xuyên tổ chức / bản đồ bất đồng" của
  bạn đã có tiền lệ đang hoạt động ở phạm vi đơn tổ chức tại đây.

**Xác minh, phê duyệt, kiểm toán, trôi dạt (Engine 07 — các láng giềng của lợi thế phòng thủ).**
- **#12/#26** các cổng phê duyệt; **#51** *kiểm toán phiên đo được 112/129 lần vi phạm
  cổng*; **#54** Ombre tuân thủ EU-AI-Act (kiểm toán chống giả mạo, phát hiện ảo giác);
  **#190** STANDING.md để agent ngừng đoán mò; **#154** đánh giá bảo mật.
  → Hệ sinh thái đang *với tới các nguyên hàm về niềm tin/kiểm toán* nhưng **không ai đo
  độ trung thành so với một chuyên gia được nêu tên cụ thể**. Đây là xác nhận rõ ràng
  nhất rằng Engine 07 là khoảng trống thị trường mở **ngay cả bên trong cộng đồng Hermes.**

**Bộ nhớ như một tài sản bền vững, có thể kiểm tra (Engine 02).**
- **#31/#53/#75/#86/#106/#119/#136/#160** — cả một ngành thủ công gồm các memory kernel
  (đồ thị SQLite, phân rã theo thời gian, giải quyết mâu thuẫn, "có thể kiểm tra, không
  phải hộp đen"). → "Sổ nghề đi theo twin" của bạn là *cùng một nhu cầu*, ở một tầng cao
  hơn. Nhiều khả năng bạn có thể **áp dụng/hợp tác** thay vì xây dựng bộ nhớ từ đầu.

**Thương mại / danh tính / chứng thực (Engine 05 & 09).**
- **#204 Merxex** = lớp **thương mại** agent-với-agent; **#97** = **danh tính onchain +
  chứng thực proof-of-work** (Ethereum Attestation Service). → Các phiên bản sơ khai của
  engine danh tính + royalty của bạn đã tồn tại để xây dựng lên trên hoặc học hỏi. Chứng
  thực on-chain là một cơ chế ứng viên cho xuất xứ/huy hiệu có bằng chứng chống giả mạo.

**Tự động sinh skill & một chuẩn skill (Engine 06 + kệ registry).**
- **#207 Skill Factory** và **#67** skill-audit tự cải thiện; chuẩn **#146 agentskills.io**
  + kệ registry/xếp hạng **#183 Hermes Atlas**. → "Xưởng lấp đầy kệ" và một **chuẩn skill
  + registry có xếp hạng đã tồn tại** trong thế giới Hermes. Registry của bạn nên
  **tương tác với agentskills.io**, chứ không phát minh lại nó — và bổ sung thứ duy nhất
  mà xếp hạng sao của Atlas thiếu: *độ trung thành đo được + hạn dùng.*

## D. Hermes và OpenClaw được triển khai cùng nhau

OpenClaw liên tục xuất hiện như điểm tham chiếu của Hermes — thường **chạy song song**:
- **#82** Hermes(CEO)+OpenClaw(Kỹ sư cấp cao) trên một Obsidian vault; **#123** cả hai
  trên một Mac Mini; **#181** Hermes làm *watchdog trên OpenClaw*; **#126** Hermes xử lý
  sự cố cho OpenClaw; **#132/#151/#180** người dùng chuyển từ OpenClaw nhưng port lại các
  tính năng của nó.
- **Di trú/chuyển đổi:** **#27** primeclaws.com chuyển đổi giữa hai bên; **#145**
  "đã chuyển, không nhìn lại"; **#187** "Hermes là OpenClaw + 1 tuần debug + RAG +
  bộ nhớ"; **#163** "mỗi bản cập nhật OpenClaw đều làm hỏng thứ gì đó"; **#211** công cụ
  di trú shadow-to-live; **#211/#151** các bản port ngang bằng tính năng.

**Hàm ý:** hai hệ sinh thái này **tương tác được và kề cận nhau**, và người dùng đã chạy
các thiết lập đa runtime. Một nền tảng **độc lập runtime ở tầng twin** (kiểm định/host
twin bất kể runtime bên dưới là Hermes hay OpenClaw) là một định vị đáng tin — nó cưỡi
trên thực tế rằng người dùng đã trộn lẫn chúng. Những startup tận dụng *cả hai* hiện nay
hầu hết là **những power user cá nhân và công cụ nhỏ** (primeclaws.com, các thiết lập
watchdog, tiện ích di trú), chứ không phải các công ty được cấp vốn — tức là ô "twin-trên-
nhiều-runtime" **vẫn còn để trống.**

## E. Danh mục này có ý nghĩa gì với Brain4All

1. **Nhu cầu đã được chứng minh và rộng khắp.** 262 story thật, có nguồn, trải khắp 15
   lĩnh vực, phần lớn từ cá nhân và nhóm nhỏ — thị trường *agent cá nhân/chuyên gia tự lưu
   trữ* đang sống, không phải suy đoán.
2. **Mọi engine hàng hóa đều đã bão hòa** bởi cộng đồng (memory kernel, dashboard,
   connector, routing, sandbox). **Đừng xây những thứ này** — hãy áp dụng, hợp tác, hoặc
   cưỡi theo chuẩn (agentskills.io, Atlas, Merxex, EAS). Xác nhận các tài liệu công nghệ
   lõi & đối thủ (03) và lộ trình (08): thuê engine hàng hóa.
3. **Lợi thế phòng thủ được xác nhận là trống ngay từ bên trong.** Cộng đồng đang *tích
   cực xây các nguyên hàm kiểm toán, phê duyệt, tuân thủ và niềm tin* (#51, #54, #154,
   #190) — nhưng **không một ai đo liệu một twin có tái tạo trung thành một con người được
   nêu tên hay không.** Engine 07 là khoảng trống thị trường ngay cả giữa những người gần
   công nghệ nhất.
4. **Nhân bản giọng/phán đoán cá nhân đã là use case sát thủ** (#123, #141, #155, #150) —
   nó chỉ thiếu kiểm định, xuất xứ và royalty. Đó chính xác là mũi nhọn của Twin Terminal,
   và điều đó nghĩa là **bạn đang mở rộng một hành vi đã được chứng minh, chứ không phát
   minh ra nhu cầu.**
5. **Tương tác, đừng phát minh lại registry.** agentskills.io + Hermes Atlas là những kẻ
   dẫn đầu hiện hữu của "cái kệ." Sự khác biệt của bạn là **độ trung thành đo được +
   hạn dùng + lineage royalty** xếp chồng lên trên (hoặc bắc cầu tới) chuẩn đó.
6. **Nous đã kiếm tiền từ hosting (Hermify).** Củng cố tài liệu công nghệ lõi & đối thủ
   (03): đừng cạnh tranh về hosting; hãy cạnh tranh về kiểm định.
