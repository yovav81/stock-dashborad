#!/usr/bin/env python3
"""
Collector script for updating dashboard-data.json.

This script reads watchlist.json and retrieves:
- Daily adjusted closing prices from Alpha Vantage
- Recent news articles from News API
- Recent filings from regulatory sites (currently SEC for US tickers and TASE for IL tickers)

It writes the aggregated data to dashboard-data.json in the same directory.

Before running, set the following environment variables or replace with your API keys:
- ALPHA_VANTAGE_API_KEY
- NEWS_API_KEY

Run this script daily via scheduler (e.g., GitHub Actions) to keep the dashboard up to date.
"""
import json
import os
import datetime
from datetime import timezone
import requests

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "YOUR_ALPHA_VANTAGE_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "YOUR_NEWS_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "dashboard-data.json")

# Helper: get start dates

def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def fetch_prices(ticker):
    # Use Alpha Vantage TIME_SERIES_DAILY_ADJUSTED endpoint
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}&outputsize=full"
    resp = requests.get(url, timeout=30)
    data = resp.json().get("Time Series (Daily)", {})
    return data


def calculate_returns(series, week_ago, month_start, year_start):
    dates = sorted(series.keys(), reverse=True)
    today_price = None
    week_price = None
    month_price = None
    year_price = None
    for date_str in dates:
        price = float(series[date_str]["5. adjusted close"])
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        if today_price is None:
            today_price = price
        if week_price is None and date <= week_ago:
            week_price = price
        if month_price is None and date <= month_start:
            month_price = price
        if year_price is None and date <= year_start:
            year_price = price
        if week_price and month_price and year_price:
            break
    def pct_change(current, past):
        return ((current - past) / past * 100.0) if past else 0.0
    return {
        "week_change_pct": pct_change(today_price, week_price),
        "month_change_pct": pct_change(today_price, month_price),
        "ytd_change_pct": pct_change(today_price, year_price)
    }


def fetch_news(company_name, ticker):
    # Query string combining company name and ticker
    query = f"{company_name} OR {ticker}"
    url = (
        f"https://newsapi.org/v2/everything?"
        f"q={requests.utils.quote(query)}&"
        f"language=en&"
        f"sortBy=publishedAt&"
        f"pageSize=5&"
        f"apiKey={NEWS_API_KEY}"
    )
    resp = requests.get(url, timeout=30)
    articles = []
    if resp.status_code == 200:
        for article in resp.json().get("articles", []):
            articles.append({
                "title": article.get("title"),
                "source": article.get("source", {}).get("name"),
                "published_at": article.get("publishedAt"),
                "url": article.get("url")
            })
    return articles


def fetch_filings_us(ticker):
    # Placeholder: implement mapping from ticker to CIK and retrieving recent filings from SEC
    return []


def fetch_filings_il(ticker):
    # Placeholder for TASE Data Hub or Maya API
    return []


def main():
    week_ago, month_start, year_start = get_date_ranges()
    with open(WATCHLIST_FILE, 'r') as f:
        watchlist = json.load(f)
    performance = []
    news = {}
    filings = {}
    for item in watchlist:
        ticker = item['ticker']
        market = item.get('market', 'US')
        company_name = item.get('company', ticker)
        # Prices
        try:
            price_series = fetch_prices(ticker)
            returns = calculate_returns(price_series, week_ago, month_start, year_start)
        except Exception as e:
            print(f"Error fetching prices for {ticker}: {e}")
            returns = {"week_change_pct": 0.0, "month_change_pct": 0.0, "ytd_change_pct": 0.0}
        performance.append({"ticker": ticker, **returns})
        # News
        try:
            news_list = fetch_news(company_name, ticker)
            news[ticker] = news_list
        except Exception as e:
            print(f"Error fetching news for {ticker}: {e}")
            news[ticker] = []
        # Filings
        if market.upper() == 'US':
            filings[ticker] = fetch_filings_us(ticker)
        elif market.upper() == 'IL':
            filings[ticker] = fetch_filings_il(ticker)
        else:
            filings[ticker] = []
    output = {
        "updated_at": datetime.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "performance": performance,
        "news": news,
        "filings": filings,
    }
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"Saved updated data to {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
