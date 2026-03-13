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

SEC_HEADERS = {
    "User-Agent": "stock-dashboard yovav81@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}


def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def fetch_prices(ticker: str):
    print(f"Fetching prices for {ticker}")

    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
    except Exception as e:
        print(f"yfinance failed for {ticker}: {e}")
        return {}

    if hist is None or hist.empty:
        print(f"No price data returned for {ticker}")
        return {}

    if "Close" not in hist.columns:
        print(f"No Close column for {ticker}. Columns: {list(hist.columns)}")
        return {}

    data = {}

    for idx, row in hist.iterrows():
        try:
            date_str = idx.strftime("%Y-%m-%d")
        except Exception:
            date_str = str(idx)[:10]

        try:
            close_val = float(row["Close"])
            data[date_str] = {"close": close_val}
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


def fetch_news(company_name: str, ticker: str):
    if not NEWS_API_KEY:
        print(f"No NEWS_API_KEY set. Skipping news for {ticker}")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": f"\"{company_name}\" OR {ticker}",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWS_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        print(f"News API status for {ticker}: {resp.status_code}")
    except Exception as e:
        print(f"News request failed for {ticker}: {e}")
        return []

    if resp.status_code != 200:
        print(f"News API error for {ticker}: {resp.text[:300]}")
        return []

    try:
        payload = resp.json()
    except Exception as e:
        print(f"Could not parse news JSON for {ticker}: {e}")
        return []

    articles = []
    for article in payload.get("articles", []):
        articles.append(
            {
                "title": article.get("title"),
                "source": article.get("source", {}).get("name"),
                "published_at": article.get("publishedAt"),
                "url": article.get("url"),
            }
        )

    print(f"{ticker}: loaded {len(articles)} news articles")
    return articles


def fetch_sec_company_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"

    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    mapping = {}
    for item in data.values():
        ticker = item.get("ticker", "").upper()
        cik = str(item.get("cik_str", "")).zfill(10)
        if ticker:
            mapping[ticker] = cik

    print(f"Loaded {len(mapping)} SEC ticker->CIK mappings")
    return mapping


def build_sec_filing_url(cik: str, accession_number: str, primary_document: str):
    cik_no_leading = str(int(cik))
    accession_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading}/{accession_nodash}/{primary_document}"


def fetch_filings_us(ticker: str, ticker_to_cik: dict):
    cik = ticker_to_cik.get(ticker.upper())
    if not cik:
        print(f"No CIK found for {ticker}")
        return []

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"SEC filings fetch failed for {ticker}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    wanted_forms = {"10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A"}
    filings = []

    for form, filing_date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
        if form not in wanted_forms:
            continue

        filing_url = build_sec_filing_url(cik, accession, primary_doc)
        filings.append(
            {
                "type": form,
                "date": filing_date,
                "title": form,
                "url": filing_url,
            }
        )

        if len(filings) >= 5:
            break

    print(f"{ticker}: loaded {len(filings)} SEC filings")
    return filings


def fetch_filings_il(ticker: str):
    return []


def main():
    week_ago, month_start, year_start = get_date_ranges()

    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    performance = []
    news = {}
    filings = {}

    us_tickers = [
        item["ticker"]
        for item in watchlist
        if item.get("market", "US").upper() == "US"
    ]

    ticker_to_cik = fetch_sec_company_tickers() if us_tickers else {}

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

        performance.append({"ticker": ticker, **returns})

        try:
            news[ticker] = fetch_news(company_name, ticker)
        except Exception as e:
            print(f"News fetch failed for {ticker}: {e}")
            news[ticker] = []

        if market.upper() == "US":
            filings[ticker] = fetch_filings_us(ticker, ticker_to_cik)
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
