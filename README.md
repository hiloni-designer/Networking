# LinkedIn AI Agent

Automated LinkedIn outreach for full-time job hunting — built with Playwright, Claude AI, FastAPI, and a human review dashboard.

---

## What it does

| Step | What happens |
|---|---|
| **Discovery** | Searches LinkedIn with your target queries, scrapes profiles |
| **Enrichment** | Fetches and reads linked websites for richer context |
| **Classification** | Claude assigns each profile a persona + match score (0–10) |
| **Rate limiting** | Max 15 invites/day (safe), randomised human-like delays |
| **Connection monitor** | Checks every hour for newly accepted connections |
| **Message generation** | Claude writes a personalised 300-char message per persona |
| **Human review** | You approve / edit / reject in the web dashboard |
| **Send** | Approved messages are delivered automatically |

### Persona → Message angle

| Persona | Outreach angle |
|---|---|
| Senior Designer | Peer conversation — shared craft |
| Design Leader | Acknowledge their team + ask about openings |
| Recruiter | Signal you're open — make it easy for them |
| HR | Professional intro, ask about design openings |
| Startup Founder | Genuine interest in their product + your value |
| Product Manager | Design + PM collaboration |
| Fintech Professional | Design in complex products — your speciality |

---

## Quick start (local)

### 1. Clone and install

```bash
git clone <repo-url>
cd linkedin_agent
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — fill in your Anthropic API key and LinkedIn credentials
```

**Get your Anthropic API key:** https://console.anthropic.com

**Minimum .env to fill in:**
```
ANTHROPIC_API_KEY=sk-ant-...
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=yourpassword
YOUR_NAME=Your Name
YOUR_ROLE=UI/UX Designer
YOUR_EXPERIENCE_YEARS=3
```

### 3. Run

```bash
python main.py
```

Open **http://localhost:8000** in your browser — the dashboard is live.

---

## Deployment on Railway (free tier)

1. Create account at https://railway.app
2. New Project → Deploy from GitHub repo
3. Add all environment variables from `.env.example` in Railway's Variables tab
4. Railway auto-detects the `Dockerfile` and builds
5. Your dashboard will be at the Railway-provided URL

**Important for Railway:** Set `DATABASE_URL=sqlite:///./linkedin_agent.db` and attach a Volume to persist data across deploys.

## Deployment on Render (free tier)

1. Create account at https://render.com
2. New → Web Service → connect your GitHub repo
3. Render detects `render.yaml` automatically
4. Add secret env vars (API key, LinkedIn credentials) in Render dashboard
5. Deploy

**Note:** Render's free tier spins down after 15 min inactivity. Use their $7/month Starter plan to keep it always-on, or use Railway which keeps free services running.

---

## Dashboard guide

### Review Queue
Every message Claude generates appears here before being sent.

- **Approve** — sends as-is on the next send cycle (every 30 min)
- **Edit** — click to make inline edits, then Save & Approve
- **Reject** — discards the message

The character counter turns amber at 240 chars and red at 280 (LinkedIn limit is 300).

### Jobs
Trigger any job manually:
- **Discovery** — runs a search round right now
- **Monitor** — checks for new connections right now
- **Send messages** — dispatches all approved messages right now

### Profiles
Filter by status or persona, search by name/company. Click "Open ↗" to view their LinkedIn profile directly.

---

## Tuning the agent

### Change daily invite limit
```
MAX_INVITES_PER_DAY=10   # Start low, increase over 2 weeks
```

### Add or remove search queries
```
SEARCH_QUERIES=Senior Designer,Design Director,Head of Design,UX Lead
```

### Change persona classification threshold
In `agent/orchestrator.py`, line ~80:
```python
if match_score < 4.0 or persona_str == "unknown":
```
Raise to `5.0` to be more selective, lower to `3.0` to cast a wider net.

### Tune message prompts
Edit `prompts/claude_prompts.py`:
- `PERSONA_ANGLES` — change the message angle and CTA per persona
- `MESSAGE_SYSTEM` — change the overall tone rules
- `CLASSIFICATION_SYSTEM` — change scoring criteria

---

## Safety guidelines

LinkedIn actively monitors for automation. Stay safe:

| Rule | Why |
|---|---|
| Start at 10 invites/day | Ramp up slowly over 2 weeks |
| Never run on weekends | Looks unnatural |
| Randomised delays (8–30s) | Built in — don't reduce |
| Use a warmed-up account | 200+ connections baseline |
| Don't run 24/7 non-stop | Take breaks, like a human |
| Keep profile complete | Reduces "bot" suspicion |

If LinkedIn flags your account, **stop immediately** and wait 48–72 hours before resuming at a lower rate.

---

## Project structure

```
linkedin_agent/
├── main.py                    # Entrypoint
├── requirements.txt
├── Dockerfile
├── railway.toml / render.yaml
├── .env.example
│
├── config/
│   └── settings.py            # All configuration
│
├── database/
│   └── models.py              # SQLAlchemy models + DB setup
│
├── prompts/
│   └── claude_prompts.py      # All Claude prompts (tune here)
│
├── agent/
│   ├── linkedin_browser.py    # Playwright automation
│   ├── ai_service.py          # Claude API — classify + generate
│   ├── web_scraper.py         # Website scraper (trafilatura)
│   ├── rate_limiter.py        # Daily limit enforcement
│   └── orchestrator.py        # Main job logic
│
├── api/
│   └── server.py              # FastAPI backend + scheduler
│
└── dashboard/
    └── index.html             # Review dashboard UI
```

---

## Tech stack (all free)

| Component | Tool | Cost |
|---|---|---|
| Browser automation | Playwright | Free |
| AI classification + messages | Claude Sonnet via Anthropic API | Pay per use (~$0.01–0.05/day) |
| Web scraping | trafilatura | Free |
| Backend | FastAPI + APScheduler | Free |
| Database | SQLite | Free |
| Hosting | Railway / Render | Free tier |

Anthropic API cost estimate: ~100 profiles/day × ~$0.003 per classify+message = **~$0.30/day** at typical usage.

---

## Troubleshooting

**CAPTCHA / checkpoint on login**
LinkedIn may ask for a verification code. Run locally with `headless=False` (change in `linkedin_browser.py`), complete the CAPTCHA manually, then switch back to headless.

**"No Connect button found"**
Profile may already be connected, or is a 3rd-degree connection requiring InMail. The agent logs and skips these automatically.

**Messages not sending**
Check the dashboard → Logs tab. If approved messages exist but aren't sending, trigger the "Send messages" job manually.

**Profile data missing (no role/company)**
LinkedIn's DOM changes frequently. The scraper uses multiple selector fallbacks but may need updating. Check the profile manually and update selectors in `agent/linkedin_browser.py`.
