# 🔍 Crypto Job Hunter — Phase 1

Aggregates job listings from 18+ crypto job boards, scores them against your profile, and sends a daily digest.

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/notwitcheer/Web3-Job-Hunter.git
cd Web3-Job-Hunter

# 2. Set up virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your profile
# Copy config.yaml to config_personal.yaml and customize:
cp config.yaml config_personal.yaml
# Edit: title_keywords, preferred_keywords, exclude_keywords, location preferences
# Optional: Add Discord webhook URL for notifications

# 5. Test with dry run (preview mode)
python job_hunter.py --dry-run --verbose

# 6. Run for real
python job_hunter.py

# 7. Check results
# View console output, HTML report, and jobs.db for deduplication
```

## ⚡ Performance

Based on recent testing:
- **330+ jobs scraped** from multiple sources in ~20 seconds
- **Block**: 308 jobs found ✅
- **a16z**: 22 jobs found ✅
- **19 qualified matches** for community/growth/marketing roles
- **HTML report generation**: Professional dark-themed output
- **SQLite deduplication**: Fast and reliable

## How It Works

### Scraping Layer
The script scrapes 18 job boards using 4 strategies:

| Strategy | Sites | Reliability |
|----------|-------|-------------|
| **Lever API** | Solana, Avax, BNB Chain | ⭐⭐⭐ Very reliable |
| **Greenhouse API** | Block, a16z, Animoca | ⭐⭐⭐ Very reliable |
| **Ashby API** | Dragonfly, Pantera | ⭐⭐⭐ Reliable |
| **HTML scraping** | web3.career, cryptojobslist, etc. | ⭐⭐ May break if site changes |

### Scoring Engine
Each job gets a score from 0-100 based on:

- **Title match (35%)** — How well the job title matches your keywords
- **Keyword match (30%)** — How many preferred keywords appear in the description
- **Location match (15%)** — Whether the location matches your preferences
- **Recency (20%)** — How recently the job was posted

### Deduplication
Uses SQLite to track every job seen. Running the script multiple times will only notify you about **new** jobs.

### Notifications
- **Console** — Rich formatted table in terminal
- **Discord webhook** — Embed with top matches (add your webhook URL in config)
- **HTML report** — Slick dark-theme report file

## 🚀 Quick Run

```bash
# Run the job hunter (opens HTML report automatically)
./hunt.sh

# Or set up a global command (one-time setup)
./setup_alias.sh
source ~/.zshrc  # or ~/.bashrc
jobhunt  # Run from anywhere!
```

The HTML report opens automatically with:
- **Clickable job titles** → Direct links to applications
- **Score-based sorting** → Best matches first
- **New job highlighting** → See what's fresh
- **Dark theme** → Easy on the eyes

## Config Reference

See `config.yaml` for all options. Key sections:

- `filters.title_keywords` — Roles you're looking for
- `filters.preferred_keywords` — Terms that boost a job's score
- `filters.exclude_keywords` — Terms that auto-exclude a job
- `filters.location` — Remote preference, preferred/excluded locations
- `scoring.min_score` — Minimum score to include (0-100)
- `scoring.max_results` — Cap on number of results
- `notification.discord_webhook` — Your Discord webhook URL
- `sites.*` — Enable/disable individual job boards

## Troubleshooting

**"No jobs found from X site"**
- The HTML scraper for that site may need updating (site redesign). The API-based scrapers (Lever, Greenhouse, Ashby) are much more stable.
- Check with `--verbose` flag for error details.

**Lever/Greenhouse/Ashby scraper returns 0 jobs**
- The company slug might have changed. Check the actual URL and update the slug in the `_init_scrapers()` method.

**Rate limiting**
- Increase `scraping.request_delay` in config (default: 2 seconds between requests)

## Architecture

```
config.yaml          → Your filters, preferences, site toggles
job_hunter.py        → Main script
├── HttpClient       → Rate-limited HTTP with retries
├── Scrapers         → One per site/platform type
│   ├── LeverScraper
│   ├── GreenhouseScraper
│   ├── AshbyScraper
│   └── HTML scrapers (per-site)
├── ScoringEngine    → Scores jobs 0-100 against your profile
├── JobDatabase      → SQLite dedup + history tracking
└── Notifier         → Console, Discord, HTML report
jobs.db              → Auto-created SQLite database
```
