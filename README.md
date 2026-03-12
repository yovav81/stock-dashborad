# Stock Dashboard Project

This repository contains a simple stock performance dashboard that pulls daily data for a set of tickers and displays performance metrics, news headlines, and recent regulatory filings. It is intended to be hosted as a static site (e.g., with GitHub Pages) with a scheduled workflow that updates the underlying data each day.

## Structure

- **index.html** – Front‑end dashboard page. It loads `dashboard-data.json` and `watchlist.json` to render performance tables, news sections, and filings. The watchlist can be edited from the page (client‑side only).
- **watchlist.json** – List of tickers to track. Includes ticker symbol, market (US/IL), and company name. Edit this file to add or remove companies.
- **collector.py** – Python script that fetches daily adjusted closing prices from Alpha Vantage, news from NewsAPI, and placeholders for filings (US via SEC, IL via TASE). It produces `dashboard-data.json` containing the latest data.
- **.github/workflows/update.yml** – GitHub Actions workflow that runs `collector.py` every day at 06:00 UTC (08:00 in Israel) and commits the updated `dashboard-data.json` back to the repository.
- **dashboard-data.json** – Generated data file (initially absent). Created by the collector script.

## How to Use

1. **Generate API keys**:
   - Sign up for an API key at [Alpha Vantage](https://www.alphavantage.co) for stock price data.
   - Sign up for an API key at [NewsAPI](https://newsapi.org) for news headlines.
   - Optionally, implement `fetch_filings_us` and `fetch_filings_il` in `collector.py` to pull regulatory filings. This may require additional APIs (e.g., SEC’s data API or TASE/Maya for Israeli companies).

2. **Edit the watchlist**:
   - Modify `watchlist.json` to include the ticker symbols, markets, and company names you wish to track.

3. **Deploy**:
   - Push this repository to GitHub.
   - In the repository settings, enable GitHub Pages to serve the `stocks_dashboard` directory.
   - Add your API keys as secrets in the GitHub repository (`ALPHA_VANTAGE_API_KEY`, `NEWS_API_KEY`).

4. **Schedule updates**:
   - The workflow defined in `.github/workflows/update.yml` will run daily at 06:00 UTC. It installs dependencies, runs the collector script, commits the updated `dashboard-data.json`, and pushes the change back to the repository.

5. **View the dashboard**:
   - Once GitHub Pages is enabled and the workflow has generated `dashboard-data.json`, navigate to the GitHub Pages URL to view the dashboard.

## Notes

- The collector script currently contains placeholder functions for fetching regulatory filings. You will need to implement these parts using official APIs (e.g., SEC’s EDGAR system for US companies, TASE Data Hub for Israeli companies) or other sources.
- For initial development and testing, you can run the script locally (`python collector.py`) to produce `dashboard-data.json` and open `index.html` directly in your browser.
