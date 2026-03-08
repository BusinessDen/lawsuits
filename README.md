# Colorado Business Lawsuit Tracker

Automated tracker that monitors federal court filings in Colorado for business-relevant lawsuits. Part of the [BusinessDen](https://businessden.com) data tools suite.

## How It Works

1. **Scraper** (`scraper.py`) runs daily via GitHub Actions at 6am MT
2. Pulls new filings from [CourtListener](https://www.courtlistener.com/) API for:
   - U.S. District Court for Colorado (`cod`)
   - U.S. Bankruptcy Court for Colorado (`cob`)
3. Filters for business-relevant cases using:
   - Federal nature-of-suit codes (contract, IP, securities, bankruptcy, fraud, etc.)
   - Party name pattern matching (LLC, Inc, Corp, etc.)
   - Watched entity list (major Denver/Colorado companies)
4. Generates AI summaries and newsworthiness scores (1-5) via Claude API
5. Publishes to GitHub Pages dashboard

## Setup

### Required Secrets (GitHub repo settings)

- `COURTLISTENER_TOKEN` — Free API token from [CourtListener](https://www.courtlistener.com/) (create account → profile → API token)
- `ANTHROPIC_API_KEY` — API key for AI summaries (optional but recommended)

### Files

- `scraper.py` — Main scraper/filter/summarizer
- `index.html` — Dashboard UI
- `lawsuit-data.json` — Case data (auto-generated)
- `watched-entities.json` — Companies/people to always flag
- `.github/workflows/scrape.yml` — Automation

### Running Locally

```bash
export COURTLISTENER_TOKEN=your_token_here
export ANTHROPIC_API_KEY=your_key_here  # optional
python3 scraper.py
```

## Customization

### Watched Entities
Edit `watched-entities.json` to add companies or people you want to always flag, regardless of other filters.

### Nature-of-Suit Codes
The `BUSINESS_NOS_CODES` dict in `scraper.py` defines which federal case types are considered business-relevant. See [USCOURTS NOS codes](https://www.uscourts.gov/sites/default/files/js_044_style.pdf) for the full list.

### Scoring
The AI scoring prompt in `anthropic_summarize()` can be tuned to match your editorial priorities.

## Data Source

All court data comes from [CourtListener](https://www.courtlistener.com/) by [Free Law Project](https://free.law/), a 501(c)(3) nonprofit. The RECAP Archive contains federal court filings contributed by users of the RECAP browser extension plus all free PACER content.

**Coverage note:** Not every PACER filing is in the RECAP archive. Case metadata (names, parties, dates, nature of suit) is generally complete, but full document text may not always be available.
