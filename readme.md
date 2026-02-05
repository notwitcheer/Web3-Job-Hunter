# 🔍 Crypto Job Hunter — Phase 1

Aggregates job listings from 18+ crypto job boards, scores them against your profile, and sends a daily digest.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Edit your filters
# Open config.yaml and customize:
#   - title_keywords (what roles you want)
#   - preferred_keywords (boost score for these)
#   - exclude_keywords (skip these roles)
#   - location preferences
#   - Discord webhook URL (optional)

# 3. Run it
python job_hunter.py

# 4. Dry run (preview without notifications)
python job_hunter.py --dry-run

# 5. Verbose mode (debug)
python job_hunter.py -v
```

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

## Automate with Cron

Run daily at 9 AM:
```bash
# Edit crontab
crontab -e

# Add this line (adjust paths)
0 9 * * * cd /path/to/crypto_job_hunter && python job_hunter.py >> hunter.log 2>&1
```

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
