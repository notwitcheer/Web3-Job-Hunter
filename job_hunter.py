#!/usr/bin/env python3
"""
= Crypto Job Hunter  Phase 1

Aggregates job listings from 18+ crypto job boards, scores them against your profile,
and sends a daily digest.
"""

import asyncio
import sqlite3
import time
import argparse
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Union
import yaml
import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, TaskID
from rich import print as rprint
import json
import re


@dataclass
class Job:
    """Represents a job listing with all relevant information"""
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted_date: Optional[datetime] = None
    experience_level: Optional[str] = None
    job_type: Optional[str] = None  # full-time, part-time, contract
    salary_range: Optional[str] = None
    source: str = ""
    score: float = 0.0
    job_id: Optional[str] = None  # unique identifier from source

    def __post_init__(self):
        if not self.job_id:
            # Generate a unique ID based on title, company, and URL
            content = f"{self.title}_{self.company}_{self.url}"
            self.job_id = hashlib.md5(content.encode()).hexdigest()

    @property
    def age_days(self) -> Optional[int]:
        """Returns the age of the job posting in days"""
        if self.posted_date:
            return (datetime.now() - self.posted_date).days
        return None


class HttpClient:
    """Rate-limited HTTP client with retries and respect for robots.txt"""

    def __init__(self, request_delay: float = 2.0, timeout: float = 30.0,
                 max_retries: int = 2, user_agent: str = None):
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.last_request_time = {}  # domain -> timestamp

        default_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        self.user_agent = user_agent or default_ua

        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True
        )

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Perform rate-limited GET request with retries"""
        domain = httpx.URL(url).host

        # Rate limiting per domain
        if domain in self.last_request_time:
            elapsed = time.time() - self.last_request_time[domain]
            if elapsed < self.request_delay:
                await asyncio.sleep(self.request_delay - elapsed)

        for attempt in range(self.max_retries + 1):
            try:
                self.last_request_time[domain] = time.time()
                response = await self.client.get(url, **kwargs)
                response.raise_for_status()
                return response

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                if attempt == self.max_retries:
                    raise e

                # Exponential backoff
                delay = (2 ** attempt) * self.request_delay
                await asyncio.sleep(delay)

        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries + 1} attempts")

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


class JobDatabase:
    """SQLite database for job deduplication and history tracking"""

    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Initialize the SQLite database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    url TEXT NOT NULL,
                    description TEXT,
                    posted_date TIMESTAMP,
                    experience_level TEXT,
                    job_type TEXT,
                    salary_range TEXT,
                    source TEXT,
                    score REAL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_new BOOLEAN DEFAULT 1
                )
            """)

            # Index for faster lookups
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_id ON jobs(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON jobs(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON jobs(first_seen)")

    def is_new_job(self, job: Job) -> bool:
        """Check if this job is new (not seen before)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job.job_id,))
            return cursor.fetchone() is None

    def save_job(self, job: Job, is_new: bool = True):
        """Save or update job in database"""
        with sqlite3.connect(self.db_path) as conn:
            if is_new:
                conn.execute("""
                    INSERT OR REPLACE INTO jobs
                    (job_id, title, company, location, url, description, posted_date,
                     experience_level, job_type, salary_range, source, score, is_new)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job.job_id, job.title, job.company, job.location, job.url,
                    job.description, job.posted_date, job.experience_level,
                    job.job_type, job.salary_range, job.source, job.score, is_new
                ))
            else:
                conn.execute("""
                    UPDATE jobs SET last_seen = CURRENT_TIMESTAMP, score = ?
                    WHERE job_id = ?
                """, (job.score, job.job_id))

    def get_new_jobs_count(self) -> int:
        """Get count of new jobs in this run"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_new = 1")
            return cursor.fetchone()[0]

    def mark_all_as_seen(self):
        """Mark all new jobs as seen (not new anymore)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE jobs SET is_new = 0 WHERE is_new = 1")


class ScoringEngine:
    """Scores jobs based on user preferences and criteria"""

    def __init__(self, config: Dict):
        self.config = config
        self.weights = config['scoring']
        self.filters = config['filters']
        self.profile = config['profile']

    def score_job(self, job: Job) -> float:
        """Calculate a score (0-100) for a job based on user preferences"""
        total_score = 0.0

        # Title match scoring (35%)
        title_score = self._score_title_match(job.title)
        total_score += title_score * (self.weights['title_match_weight'] / 100)

        # Keyword match scoring (30%)
        keyword_score = self._score_keyword_match(job.description)
        total_score += keyword_score * (self.weights['keyword_match_weight'] / 100)

        # Location match scoring (15%)
        location_score = self._score_location_match(job.location)
        total_score += location_score * (self.weights['location_match_weight'] / 100)

        # Recency scoring (20%)
        recency_score = self._score_recency(job.posted_date)
        total_score += recency_score * (self.weights['recency_weight'] / 100)

        return min(100.0, max(0.0, total_score))

    def _score_title_match(self, title: str) -> float:
        """Score how well the job title matches target keywords"""
        if not title:
            return 0.0

        title_lower = title.lower()
        matches = 0
        total_keywords = len(self.filters['title_keywords'])

        for keyword in self.filters['title_keywords']:
            if keyword.lower() in title_lower:
                matches += 1

        if total_keywords == 0:
            return 50.0  # neutral score if no keywords specified

        return (matches / total_keywords) * 100

    def _score_keyword_match(self, description: str) -> float:
        """Score based on preferred keywords in description"""
        if not description:
            return 0.0

        description_lower = description.lower()
        matches = 0
        total_keywords = len(self.filters['preferred_keywords'])

        for keyword in self.filters['preferred_keywords']:
            if keyword.lower() in description_lower:
                matches += 1

        if total_keywords == 0:
            return 50.0  # neutral score if no keywords specified

        # Cap at 100% even if more keywords match
        return min(100.0, (matches / max(1, total_keywords)) * 100)

    def _score_location_match(self, location: str) -> float:
        """Score based on location preferences"""
        if not location:
            return 50.0  # neutral if no location info

        location_lower = location.lower()
        location_config = self.filters['location']

        # Check for excluded locations first
        for excluded in location_config.get('excluded_locations', []):
            if excluded.lower() in location_lower:
                return 0.0  # Completely exclude

        # If remote_only is true, heavily favor remote positions
        if location_config.get('remote_only', False):
            remote_keywords = ['remote', 'worldwide', 'anywhere', 'distributed']
            for keyword in remote_keywords:
                if keyword in location_lower:
                    return 100.0
            return 10.0  # Low score for non-remote when remote_only is true

        # Check for preferred locations
        for preferred in location_config.get('preferred_locations', []):
            if preferred.lower() in location_lower:
                return 100.0

        return 50.0  # neutral score for other locations

    def _score_recency(self, posted_date: Optional[datetime]) -> float:
        """Score based on how recently the job was posted"""
        if not posted_date:
            return 50.0  # neutral if no date info

        days_old = (datetime.now() - posted_date).days

        if days_old <= 1:
            return 100.0  # Posted today/yesterday
        elif days_old <= 7:
            return 80.0   # Posted this week
        elif days_old <= 30:
            return 60.0   # Posted this month
        elif days_old <= 90:
            return 30.0   # Posted in last 3 months
        else:
            return 10.0   # Older than 3 months

    def should_exclude_job(self, job: Job) -> bool:
        """Check if job should be excluded based on filters"""
        # Check exclude keywords
        combined_text = f"{job.title} {job.description}".lower()
        for exclude_keyword in self.filters.get('exclude_keywords', []):
            if exclude_keyword.lower() in combined_text:
                return True

        # Check required keywords
        for required_keyword in self.filters.get('required_keywords', []):
            if required_keyword.lower() not in combined_text:
                return True

        # Check experience level filter
        experience_levels = self.filters.get('experience_levels', [])
        if experience_levels and job.experience_level:
            if job.experience_level.lower() not in [level.lower() for level in experience_levels]:
                return True

        return False


class BaseScraper:
    """Base class for all job scrapers"""

    def __init__(self, http_client: HttpClient, config: Dict):
        self.http_client = http_client
        self.config = config
        self.console = Console()

    async def scrape(self) -> List[Job]:
        """Scrape jobs from the source - to be implemented by subclasses"""
        raise NotImplementedError

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text content"""
        if not text:
            return ""

        # Remove extra whitespace and newlines
        text = re.sub(r'\s+', ' ', text.strip())

        # Remove HTML entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

        return text

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats into datetime object"""
        if not date_str:
            return None

        # Common date patterns
        patterns = [
            '%Y-%m-%d',           # 2024-01-01
            '%Y-%m-%dT%H:%M:%S',  # 2024-01-01T12:00:00
            '%Y-%m-%dT%H:%M:%SZ', # 2024-01-01T12:00:00Z
            '%B %d, %Y',          # January 01, 2024
            '%b %d, %Y',          # Jan 01, 2024
            '%m/%d/%Y',           # 01/01/2024
            '%d/%m/%Y',           # 01/01/2024 (EU format)
        ]

        for pattern in patterns:
            try:
                return datetime.strptime(date_str.strip(), pattern)
            except ValueError:
                continue

        # Handle relative dates like "2 days ago"
        if 'day' in date_str.lower():
            match = re.search(r'(\d+)\s*days?\s*ago', date_str.lower())
            if match:
                days_ago = int(match.group(1))
                return datetime.now() - timedelta(days=days_ago)

        return None


class LeverScraper(BaseScraper):
    """Scraper for Lever-based job boards (Solana, Avax, BNB Chain)"""

    def __init__(self, http_client: HttpClient, config: Dict):
        super().__init__(http_client, config)
        self.lever_companies = {
            'solana': 'solana-foundation-8fd8',
            'avax': 'avalabs',
            'bnb_chain': 'bnbchain',
        }

    async def scrape(self) -> List[Job]:
        """Scrape all Lever-based job boards"""
        all_jobs = []

        for company_name, company_slug in self.lever_companies.items():
            if not self.config['sites'].get(f'{company_name}_jobs', True):
                continue

            try:
                jobs = await self._scrape_lever_company(company_name, company_slug)
                all_jobs.extend(jobs)
                self.console.print(f" {company_name}: {len(jobs)} jobs found")

            except Exception as e:
                self.console.print(f" {company_name}: Error - {str(e)}")

        return all_jobs

    async def _scrape_lever_company(self, company_name: str, company_slug: str) -> List[Job]:
        """Scrape jobs from a specific Lever company"""
        url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
        response = await self.http_client.get(url)

        data = response.json()
        jobs = []

        for posting in data:
            try:
                job = Job(
                    title=posting['text'],
                    company=posting['categories']['commitment'],
                    location=posting['categories']['location'] or 'Remote',
                    url=posting['hostedUrl'],
                    description=posting['description'],
                    posted_date=self._parse_date(posting['createdAt']),
                    job_type=posting['categories']['commitment'],
                    source=f"lever_{company_name}",
                    job_id=posting['id']
                )

                jobs.append(job)

            except (KeyError, TypeError) as e:
                # Skip malformed job postings
                continue

        return jobs


class GreenhouseScraper(BaseScraper):
    """Scraper for Greenhouse-based job boards (Block, a16z, Animoca)"""

    def __init__(self, http_client: HttpClient, config: Dict):
        super().__init__(http_client, config)
        self.greenhouse_companies = {
            'block': 'block',
            'a16z': 'a16z',
            'animoca': 'animocabrands',
        }

    async def scrape(self) -> List[Job]:
        """Scrape all Greenhouse-based job boards"""
        all_jobs = []

        for company_name, company_slug in self.greenhouse_companies.items():
            if not self.config['sites'].get(f'{company_name}_jobs', True):
                continue

            try:
                jobs = await self._scrape_greenhouse_company(company_name, company_slug)
                all_jobs.extend(jobs)
                self.console.print(f" {company_name}: {len(jobs)} jobs found")

            except Exception as e:
                self.console.print(f" {company_name}: Error - {str(e)}")

        return all_jobs

    async def _scrape_greenhouse_company(self, company_name: str, company_slug: str) -> List[Job]:
        """Scrape jobs from a specific Greenhouse company"""
        url = f"https://api.greenhouse.io/v1/boards/{company_slug}/jobs"
        response = await self.http_client.get(url)

        data = response.json()
        jobs = []

        for posting in data['jobs']:
            try:
                location = posting['location']['name'] if posting.get('location') else 'Remote'

                job = Job(
                    title=posting['title'],
                    company=company_name.upper(),
                    location=location,
                    url=posting['absolute_url'],
                    description=posting.get('content', ''),
                    posted_date=self._parse_date(posting.get('updated_at')),
                    source=f"greenhouse_{company_name}",
                    job_id=str(posting['id'])
                )

                jobs.append(job)

            except (KeyError, TypeError) as e:
                # Skip malformed job postings
                continue

        return jobs


class AshbyScraper(BaseScraper):
    """Scraper for Ashby-based job boards (Dragonfly, Pantera)"""

    def __init__(self, http_client: HttpClient, config: Dict):
        super().__init__(http_client, config)
        self.ashby_companies = {
            'dragonfly': 'dragonfly',
            'pantera': 'pantera-capital',
        }

    async def scrape(self) -> List[Job]:
        """Scrape all Ashby-based job boards"""
        all_jobs = []

        for company_name, company_slug in self.ashby_companies.items():
            if not self.config['sites'].get(f'{company_name}_jobs', True):
                continue

            try:
                jobs = await self._scrape_ashby_company(company_name, company_slug)
                all_jobs.extend(jobs)
                self.console.print(f" {company_name}: {len(jobs)} jobs found")

            except Exception as e:
                self.console.print(f" {company_name}: Error - {str(e)}")

        return all_jobs

    async def _scrape_ashby_company(self, company_name: str, company_slug: str) -> List[Job]:
        """Scrape jobs from a specific Ashby company"""
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
        response = await self.http_client.get(url)

        data = response.json()
        jobs = []

        for posting in data.get('jobPostings', []):
            try:
                job = Job(
                    title=posting['title'],
                    company=company_name.upper(),
                    location=posting.get('locationName', 'Remote'),
                    url=f"https://jobs.ashbyhq.com/{company_slug}/{posting['id']}",
                    description=posting.get('descriptionHtml', ''),
                    posted_date=self._parse_date(posting.get('publishedDate')),
                    job_type=posting.get('employmentType'),
                    source=f"ashby_{company_name}",
                    job_id=posting['id']
                )

                jobs.append(job)

            except (KeyError, TypeError) as e:
                # Skip malformed job postings
                continue

        return jobs


class HTMLScraper(BaseScraper):
    """Scraper for HTML-based job boards (web3.career, cryptojobslist, etc.)"""

    def __init__(self, http_client: HttpClient, config: Dict):
        super().__init__(http_client, config)
        self.html_sites = {
            'web3_career': {
                'url': 'https://web3.career/jobs',
                'job_selector': '.job-tile',
                'title_selector': '.job-tile-title',
                'company_selector': '.job-tile-company',
                'location_selector': '.job-tile-location',
                'url_selector': 'a',
                'base_url': 'https://web3.career'
            },
            'crypto_careers': {
                'url': 'https://cryptocareers.com/jobs',
                'job_selector': '.job-item',
                'title_selector': '.job-title',
                'company_selector': '.company-name',
                'location_selector': '.location',
                'url_selector': 'a',
                'base_url': 'https://cryptocareers.com'
            },
            'cryptojobslist': {
                'url': 'https://cryptojobslist.com/jobs',
                'job_selector': '.job-listing',
                'title_selector': 'h3',
                'company_selector': '.company',
                'location_selector': '.location',
                'url_selector': 'a',
                'base_url': 'https://cryptojobslist.com'
            }
        }

    async def scrape(self) -> List[Job]:
        """Scrape all HTML-based job boards"""
        all_jobs = []

        for site_name, site_config in self.html_sites.items():
            if not self.config['sites'].get(site_name, True):
                continue

            try:
                jobs = await self._scrape_html_site(site_name, site_config)
                all_jobs.extend(jobs)
                self.console.print(f"✓ {site_name}: {len(jobs)} jobs found")

            except Exception as e:
                self.console.print(f"✗ {site_name}: Error - {str(e)}")

        return all_jobs

    async def _scrape_html_site(self, site_name: str, site_config: Dict) -> List[Job]:
        """Scrape jobs from a specific HTML site"""
        response = await self.http_client.get(site_config['url'])
        soup = BeautifulSoup(response.text, 'html.parser')

        jobs = []
        job_elements = soup.select(site_config['job_selector'])

        for element in job_elements[:50]:  # Limit to first 50 jobs
            try:
                title_elem = element.select_one(site_config['title_selector'])
                company_elem = element.select_one(site_config['company_selector'])
                location_elem = element.select_one(site_config['location_selector'])
                url_elem = element.select_one(site_config['url_selector'])

                if not title_elem or not company_elem:
                    continue

                title = self._clean_text(title_elem.get_text())
                company = self._clean_text(company_elem.get_text())
                location = self._clean_text(location_elem.get_text()) if location_elem else 'Remote'

                # Build full URL
                href = url_elem.get('href') if url_elem else ''
                if href.startswith('http'):
                    job_url = href
                else:
                    job_url = site_config['base_url'] + href

                job = Job(
                    title=title,
                    company=company,
                    location=location,
                    url=job_url,
                    description='',  # Could fetch full description if needed
                    posted_date=datetime.now(),  # Approximate
                    source=f"html_{site_name}",
                )

                jobs.append(job)

            except Exception as e:
                # Skip malformed job postings
                continue

        return jobs


class Notifier:
    """Handles different types of notifications (console, Discord, HTML)"""

    def __init__(self, config: Dict):
        self.config = config
        self.console = Console()

    async def send_notifications(self, jobs: List[Job], is_dry_run: bool = False):
        """Send notifications via all configured channels"""
        if not jobs:
            self.console.print("No new jobs to notify about.")
            return

        notification_config = self.config.get('notification', {})

        # Console output
        if notification_config.get('console_output', True):
            self._print_console_report(jobs, is_dry_run)

        # HTML report
        if notification_config.get('html_report', True):
            await self._generate_html_report(jobs, is_dry_run)

        # Discord webhook
        discord_webhook = notification_config.get('discord_webhook')
        if discord_webhook and not is_dry_run:
            await self._send_discord_notification(jobs, discord_webhook)

    def _print_console_report(self, jobs: List[Job], is_dry_run: bool):
        """Print formatted job report to console"""
        if is_dry_run:
            self.console.print("\n[bold yellow]🔍 DRY RUN - Job Hunter Results[/bold yellow]\n")
        else:
            self.console.print("\n[bold green]🔍 Crypto Job Hunter - New Matches![/bold green]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Score", style="cyan", width=8)
        table.add_column("Title", style="white", width=30)
        table.add_column("Company", style="green", width=20)
        table.add_column("Location", style="yellow", width=15)
        table.add_column("Source", style="blue", width=12)

        # Sort by score descending
        sorted_jobs = sorted(jobs, key=lambda j: j.score, reverse=True)

        for job in sorted_jobs[:self.config['scoring']['max_results']]:
            table.add_row(
                f"{job.score:.1f}",
                job.title[:28] + "..." if len(job.title) > 28 else job.title,
                job.company[:18] + "..." if len(job.company) > 18 else job.company,
                job.location[:13] + "..." if len(job.location) > 13 else job.location,
                job.source.replace('_', ' ').title()
            )

        self.console.print(table)
        self.console.print(f"\n[dim]Total matches: {len(sorted_jobs)}[/dim]")

        # Print top 3 jobs with URLs
        if sorted_jobs:
            self.console.print("\n[bold]🔗 Top Matches:[/bold]")
            for i, job in enumerate(sorted_jobs[:3], 1):
                self.console.print(f"{i}. {job.title} at {job.company}")
                self.console.print(f"   💼 {job.url}")
                self.console.print(f"   📊 Score: {job.score:.1f}\n")

    async def _generate_html_report(self, jobs: List[Job], is_dry_run: bool):
        """Generate HTML report file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"job_report_{timestamp}.html"

        sorted_jobs = sorted(jobs, key=lambda j: j.score, reverse=True)
        top_jobs = sorted_jobs[:self.config['scoring']['max_results']]

        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Job Hunter - {timestamp}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #e0e0e0;
            background-color: #1a1a1a;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            color: #00d4aa;
            text-align: center;
            margin-bottom: 30px;
        }}
        .summary {{
            background: #2a2a2a;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
        }}
        .job-card {{
            background: #2a2a2a;
            border-left: 4px solid #00d4aa;
            margin: 20px 0;
            padding: 20px;
            border-radius: 8px;
            transition: transform 0.2s;
        }}
        .job-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0, 212, 170, 0.1);
        }}
        .job-title {{
            color: #00d4aa;
            font-size: 1.5em;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .job-company {{
            color: #ffd700;
            font-size: 1.1em;
            margin-bottom: 5px;
        }}
        .job-meta {{
            color: #888;
            margin-bottom: 15px;
        }}
        .job-score {{
            background: #00d4aa;
            color: #1a1a1a;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: bold;
            display: inline-block;
        }}
        .job-url {{
            margin-top: 15px;
        }}
        .job-url a {{
            color: #00d4aa;
            text-decoration: none;
            border: 1px solid #00d4aa;
            padding: 8px 16px;
            border-radius: 4px;
            display: inline-block;
        }}
        .job-url a:hover {{
            background: #00d4aa;
            color: #1a1a1a;
        }}
        .dry-run {{
            background: #ff6b35;
            color: white;
            padding: 10px;
            border-radius: 4px;
            text-align: center;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Crypto Job Hunter Report</h1>

        {f'<div class="dry-run">🔍 DRY RUN - Preview Mode</div>' if is_dry_run else ''}

        <div class="summary">
            <h2>📊 Summary</h2>
            <p><strong>Generated:</strong> {datetime.now().strftime("%B %d, %Y at %I:%M %p")}</p>
            <p><strong>Total Jobs Found:</strong> {len(sorted_jobs)}</p>
            <p><strong>Jobs Displayed:</strong> {len(top_jobs)}</p>
            <p><strong>Profile:</strong> {self.config['profile']['name']}</p>
        </div>

        <h2>🎯 Top Matches</h2>
"""

        for job in top_jobs:
            html_content += f"""
        <div class="job-card">
            <div class="job-title">{job.title}</div>
            <div class="job-company">🏢 {job.company}</div>
            <div class="job-meta">
                📍 {job.location} | 🏷️ {job.source.replace('_', ' ').title()}
                {'| 📅 ' + job.posted_date.strftime('%b %d, %Y') if job.posted_date else ''}
                | 📊 <span class="job-score">{job.score:.1f}</span>
            </div>
            {f'<div style="margin: 10px 0; color: #ccc;">{job.description[:200]}...</div>' if job.description else ''}
            <div class="job-url">
                <a href="{job.url}" target="_blank">View Job 🚀</a>
            </div>
        </div>
"""

        html_content += """
    </div>
</body>
</html>"""

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)

        self.console.print(f"📄 HTML report saved: [cyan]{filename}[/cyan]")

    async def _send_discord_notification(self, jobs: List[Job], webhook_url: str):
        """Send Discord webhook notification"""
        sorted_jobs = sorted(jobs, key=lambda j: j.score, reverse=True)
        top_jobs = sorted_jobs[:5]  # Top 5 for Discord

        embed = {
            "title": "🔍 New Crypto Job Matches!",
            "color": 0x00d4aa,
            "timestamp": datetime.now().isoformat(),
            "footer": {
                "text": f"Found {len(sorted_jobs)} total matches"
            },
            "fields": []
        }

        for i, job in enumerate(top_jobs, 1):
            embed["fields"].append({
                "name": f"{i}. {job.title}",
                "value": f"🏢 {job.company}\n📍 {job.location}\n📊 Score: {job.score:.1f}\n[View Job]({job.url})",
                "inline": False
            })

        payload = {
            "username": "Crypto Job Hunter",
            "embeds": [embed]
        }

        try:
            http_client = httpx.AsyncClient()
            await http_client.post(webhook_url, json=payload)
            await http_client.aclose()
            self.console.print("✓ Discord notification sent")
        except Exception as e:
            self.console.print(f"✗ Discord notification failed: {str(e)}")


class JobHunter:
    """Main application class that coordinates all components"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()
        self.http_client = self._init_http_client()
        self.database = JobDatabase()
        self.scoring_engine = ScoringEngine(self.config)
        self.notifier = Notifier(self.config)
        self.scrapers = self._init_scrapers()
        self.console = Console()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r') as file:
                return yaml.safe_load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML config: {e}")

    def _init_http_client(self) -> HttpClient:
        """Initialize HTTP client with config settings"""
        scraping_config = self.config.get('scraping', {})
        return HttpClient(
            request_delay=scraping_config.get('request_delay', 2.0),
            timeout=scraping_config.get('timeout', 30.0),
            max_retries=scraping_config.get('max_retries', 2),
            user_agent=scraping_config.get('user_agent')
        )

    def _init_scrapers(self) -> List[BaseScraper]:
        """Initialize all scrapers"""
        scrapers = []

        # API-based scrapers (more reliable)
        if any(self.config['sites'].get(f'{company}_jobs', True)
               for company in ['solana', 'avax', 'bnb_chain']):
            scrapers.append(LeverScraper(self.http_client, self.config))

        if any(self.config['sites'].get(f'{company}_jobs', True)
               for company in ['block', 'a16z', 'animoca']):
            scrapers.append(GreenhouseScraper(self.http_client, self.config))

        if any(self.config['sites'].get(f'{company}_jobs', True)
               for company in ['dragonfly', 'pantera']):
            scrapers.append(AshbyScraper(self.http_client, self.config))

        # HTML scrapers (less reliable but higher volume)
        html_sites = ['web3_career', 'crypto_careers', 'cryptojobslist']
        if any(self.config['sites'].get(site, True) for site in html_sites):
            scrapers.append(HTMLScraper(self.http_client, self.config))

        return scrapers

    async def run(self, dry_run: bool = False, verbose: bool = False) -> Dict[str, Any]:
        """Main execution method"""
        start_time = time.time()

        if verbose:
            self.console.print("[bold green]🔍 Starting Crypto Job Hunter...[/bold green]\n")
            self.console.print(f"Profile: {self.config['profile']['name']}")
            self.console.print(f"Scrapers: {len(self.scrapers)} enabled")
            self.console.print(f"Min score threshold: {self.config['scoring']['min_score']}")
            self.console.print(f"Max results: {self.config['scoring']['max_results']}\n")

        # Scrape jobs from all sources
        all_jobs = []
        with Progress() as progress:
            task = progress.add_task("Scraping job boards...", total=len(self.scrapers))

            for scraper in self.scrapers:
                try:
                    jobs = await scraper.scrape()
                    all_jobs.extend(jobs)
                    progress.advance(task)
                except Exception as e:
                    if verbose:
                        self.console.print(f"Error with {scraper.__class__.__name__}: {str(e)}")
                    progress.advance(task)

        if verbose:
            self.console.print(f"\n📊 Scraped {len(all_jobs)} total jobs")

        # Score and filter jobs
        qualified_jobs = []
        new_jobs = []

        for job in all_jobs:
            # Skip if should be excluded
            if self.scoring_engine.should_exclude_job(job):
                continue

            # Calculate score
            job.score = self.scoring_engine.score_job(job)

            # Skip if below minimum score
            if job.score < self.config['scoring']['min_score']:
                continue

            qualified_jobs.append(job)

            # Check if it's a new job
            if self.database.is_new_job(job):
                new_jobs.append(job)

        if verbose:
            self.console.print(f"📊 {len(qualified_jobs)} jobs passed scoring threshold")
            self.console.print(f"🆕 {len(new_jobs)} are new jobs")

        # Save jobs to database (only in real run)
        if not dry_run:
            for job in qualified_jobs:
                is_new = job in new_jobs
                self.database.save_job(job, is_new)

        # Send notifications for new jobs (or all in dry run)
        notification_jobs = new_jobs if not dry_run else qualified_jobs
        if notification_jobs:
            await self.notifier.send_notifications(notification_jobs, dry_run)

        # Mark jobs as seen (only in real run)
        if not dry_run and new_jobs:
            self.database.mark_all_as_seen()

        execution_time = time.time() - start_time

        return {
            'total_scraped': len(all_jobs),
            'qualified_jobs': len(qualified_jobs),
            'new_jobs': len(new_jobs),
            'execution_time': execution_time,
            'dry_run': dry_run
        }

    async def cleanup(self):
        """Cleanup resources"""
        await self.http_client.close()


async def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description="🔍 Crypto Job Hunter")
    parser.add_argument('--dry-run', action='store_true', help='Preview mode - no database saves or Discord notifications')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--config', default='config.yaml', help='Path to config file')

    args = parser.parse_args()

    hunter = JobHunter(config_path=args.config)

    try:
        results = await hunter.run(dry_run=args.dry_run, verbose=args.verbose)

        if args.verbose or args.dry_run:
            console = Console()
            console.print(f"\n[bold green]✨ Job hunting complete![/bold green]")
            console.print(f"Total scraped: {results['total_scraped']}")
            console.print(f"Qualified: {results['qualified_jobs']}")
            console.print(f"New jobs: {results['new_jobs']}")
            console.print(f"Execution time: {results['execution_time']:.1f}s")

    except KeyboardInterrupt:
        console = Console()
        console.print("\n[yellow]Job hunt interrupted by user[/yellow]")
    except Exception as e:
        console = Console()
        console.print(f"\n[bold red]Error: {str(e)}[/bold red]")
        raise
    finally:
        await hunter.cleanup()


if __name__ == "__main__":
    asyncio.run(main())