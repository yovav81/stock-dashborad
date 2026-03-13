#!/usr/bin/env python3
import json
import os
import datetime
from datetime import timezone
import requests
import yfinance as yf

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "dashboard-data.json")


def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def fetch_prices(ticker):
    print(f"Fetching prices for {ticker}")

    hist = yf.download(
        ticker,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if hist is None or hist.empty:
        print(f"No price data returned for {ticker}")
        return {}

    data = {}

    close_col = None
    for candidate in ["Close", ("Close", ticker)]:
        if candidate in hist.columns:
            close_col = candidate
            break

    if close_col is None:
        try:
            close_series = hist["Close"]
        except Exception:
            print(f"Could not find Close column for {ticker}. Columns: {list(hist.columns)}")
            return {}
    else:
        close_series = hist[close_col]

    for date, price in close_series.items():
        date_str = str(date)[:10]
        try:
            data[date_str] = {"close": float(price)}
        except Exception as e:
            print(f"Skipping row for {ticker} on {date_str}: {e}")

    print(f"{ticker}: loaded {len(data)} price rows")
    return data


def calculate_returns(series, week_ago, month_start, year_start):
    dates = sorted(series.keys(), reverse=True)

    if not dates:
        return {
            "week_change_pct": 0.0,
            "month_change_pct": 0.0,
            "ytd_change_pct": 0.0,
        }

    today_price = None
    week_price = None
    month_price = None
    year_price = None

    for date_str in dates:
        price = float(series[date_str]["close"])
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        if today_price is None:
            today_price = price
        if week_price is None and date <= week_ago:
            week_price = price
        if month_price is None and date <= month_start:
            month_price = price
        if year_price is None and date <= year_start:
            year_price = price

    week_price = week_price or today_price
    month_price = month_price or today_price
    year_price = year_price or today_price

    def pct_change(current, past):
        if not past:
            return 0.0
        return round(((current - past) / past) * 100.0, 2)

    return {
        "week_change_pct": pct_change(today_price, week_price),
        "month_change_pct": pct_change(today_price, month_price),
        "ytd_change_pct": pct_change(today_price, year_price),
    }


def fetch_news(company_name, ticker):
    if not NEWS_API_KEY:
        print(f"No NEWS_API_KEY set. Skipping news for {ticker}")
        return []

    query = f"{company_name} OR {ticker}"
    url = (
        "https://newsapi.org/v2/everything?"
        f"q={requests.utils.quote(query)}&"
        "language=en&"
        "sortBy=publishedAt&"
        "pageSize=5&"
        f"apiKey={NEWS_API_KEY}"
    )

    try:
        resp = requests.get(url, timeout=30)
        print(f"News API status for {ticker}: {resp.status_code}")
    except Exception as e:
        print(f"News request failed for {ticker}: {e}")
        return []

    articles = []
    if resp.status_code == 200:
        for article in resp.json().get("articles", []):
            articles.append(
                {
                    "title": article.get("title"),
                    "source": article.get("source", {}).get("name"),
                    "published_at": article.get("publishedAt"),
                    "url": article.get("url"),
                }
            )
    else:
        print(f"News API returned non-200 for {ticker}: {resp.text[:300]}")

    return articles


def fetch_filings_us(ticker):
    return []


def fetch_filings_il(ticker):
    return []


def main():
    week_ago, month_start, year_start = get_date_ranges()

    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    performance = []
    news = {}
    filings = {}

    for item in watchlist:
        ticker = item["ticker"]
        market = item.get("market", "US")
        company_name = item.get("company", ticker)

        try:
            price_series = fetch_prices(ticker)
            returns = calculate_returns(price_series, week_ago, month_start, year_start)
        except Exception as e:
            print(f"Price processing failed for {ticker}: {e}")
            returns = {
                "week_change_pct": 0.0,
                "month_change_pct": 0.0,
                "ytd_change_pct": 0.0,
            }

        print(f"{ticker} returns: {returns}")
        performance.append({"ticker": ticker, **returns})

        try:
            news[ticker] = fetch_news(company_name, ticker)
        except Exception as e:
            print(f"News fetch failed for {ticker}: {e}")
            news[ticker] = []

        if market.upper() == "US":
            filings[ticker] = fetch_filings_us(ticker)
        elif market.upper() == "IL":
            filings[ticker] = fetch_filings_il(ticker)
        else:
            filings[ticker] = []

    output = {
        "updated_at": datetime.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "performance": performance,
        "news": news,
        "filings": filings,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Saved updated data to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
