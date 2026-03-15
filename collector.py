#!/usr/bin/env python3
import json
import os
import time
import datetime
from datetime import timezone

import requests
import yfinance as yf
import feedparser

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "dashboard-data.json")

SEC_HEADERS = {
    "User-Agent": "stock-dashboard yovav81@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
NEWS_URL = "https://newsapi.org/v2/everything"
MAYA_RSS_URL = "https://maya.tase.co.il/rss/companyreports"

SEC_WANTED_FORMS = {
    "10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A",
    "13D", "13G", "SC 13D", "SC 13G", "F-1", "F-3", "424B2", "425"
}

# מיפוי טיקר ישראלי -> וריאציות שם במאיה
IL_COMPANY_NAME_MAP = {
    "ELAL.TA": [
        "אל על",
        "אל-על",
        "אלעל",
        "אל על נתיבי אויר לישראל",
        "אל-על נתיבי אויר לישראל",
        "אל על נתיבי אויר לישראל בעמ",
        "אל-על נתיבי אויר לישראל בעמ",
        "EL AL",
        "El Al"
    ],
}


def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return (
        text.strip()
        .replace('"', "")
        .replace("״", "")
        .replace("'", "")
        .replace("’", "")
        .replace("־", "-")
        .replace("בע\"מ", "בעמ")
        .replace("בע׳מ", "בעמ")
        .replace("בע׳׳מ", "בעמ")
        .replace("בעמ.", "בעמ")
    )


def safe_request_json(url, headers=None, params=None, timeout=30, allow_failure=False):
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Request failed: {url} -> {e}")
        if allow_failure:
            return None
        raise


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


def dedupe_articles(items):
    seen = set()
    result = []
    for item in items:
        key = (item.get("title"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def call_news_api(query):
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 10,
        "apiKey": NEWS_API_KEY,
    }

    try:
        resp = requests.get(NEWS_URL, params=params, timeout=30)
        print(f"News API query='{query}' status={resp.status_code}")
    except Exception as e:
        print(f"News request failed for query '{query}': {e}")
        return []

    if resp.status_code != 200:
        print(f"News API error for query '{query}': {resp.text[:300]}")
        return []

    try:
        payload = resp.json()
    except Exception as e:
        print(f"Could not parse news JSON for query '{query}': {e}")
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
    return articles


def fetch_news(company_name: str, ticker: str):
    if not NEWS_API_KEY:
        print(f"No NEWS_API_KEY set. Skipping news for {ticker}")
        return []

    queries = [
        f"\"{company_name}\" OR {ticker}",
        f"{company_name} stock",
        ticker,
    ]

    all_articles = []
    for query in queries:
        all_articles.extend(call_news_api(query))
        all_articles = dedupe_articles(all_articles)
        if len(all_articles) >= 3:
            break

    result = all_articles[:3]
    print(f"{ticker}: loaded {len(result)} news articles")
    return result


def fetch_sec_company_tickers():
    data = safe_request_json(
        SEC_TICKERS_URL,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    if not data:
        print("Failed to load SEC ticker map")
        return {}

    mapping = {}
    iterable = data.values() if isinstance(data, dict) else data

    for item in iterable:
        ticker = str(item.get("ticker", "")).upper().strip()
        cik_raw = item.get("cik_str")
        if not ticker or cik_raw is None:
            continue
        cik = str(cik_raw).zfill(10)
        mapping[ticker] = cik

    print(f"Loaded {len(mapping)} SEC ticker->CIK mappings")
    return mapping


def build_sec_filing_url(cik: str, accession_number: str, primary_document: str):
    if not cik or not accession_number or not primary_document:
        return None

    cik_no_leading = str(int(cik))
    accession_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading}/{accession_nodash}/{primary_document}"


def fetch_filings_us(ticker: str, ticker_to_cik: dict):
    cik = ticker_to_cik.get(ticker.upper())
    if not cik:
        print(f"No CIK found for {ticker}")
        return []

    url = SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)

    data = safe_request_json(
        url,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    time.sleep(0.2)

    if not data:
        print(f"No SEC submissions data for {ticker}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    fallback_filings = []

    for form, filing_date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
        filing_url = build_sec_filing_url(cik, accession, primary_doc)
        item = {
            "type": form,
            "date": filing_date,
            "title": form,
            "url": filing_url or f"https://www.sec.gov/edgar/search/#/entityName={ticker}",
        }

        if form in SEC_WANTED_FORMS:
            filings.append(item)
        else:
            fallback_filings.append(item)

        if len(filings) >= 3:
            break

    if len(filings) < 3:
        for item in fallback_filings:
            filings.append(item)
            if len(filings) >= 3:
                break

    result = filings[:3]
    print(f"{ticker}: loaded {len(result)} SEC filings")
    return result


def fetch_filings_il(ticker: str):
    company_names = IL_COMPANY_NAME_MAP.get(ticker)
    if not company_names:
        print(f"No MAYA company mapping found for {ticker}")
        return []

    try:
        feed = feedparser.parse(MAYA_RSS_URL)
    except Exception as e:
        print(f"MAYA RSS failed for {ticker}: {e}")
        return []

    entries = getattr(feed, "entries", [])
    print(f"MAYA total RSS entries fetched: {len(entries)}")

    filings = []
    normalized_names = [normalize_text(name) for name in company_names]

    for entry in entries:
        title = (getattr(entry, "title", "") or "").strip()
        title_norm = normalize_text(title)

        if not any(name in title_norm for name in normalized_names):
            continue

        published = getattr(entry, "published", "") or getattr(entry, "updated", "")
        date_str = published[:10] if published else ""

        filings.append(
            {
                "type": "MAYA",
                "date": date_str,
                "title": title,
                "url": getattr(entry, "link", ""),
            }
        )

        if len(filings) >= 3:
            break

    print(f"{ticker}: loaded {len(filings)} MAYA filings")
    return filings


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

        try:
            if market.upper() == "US":
                filings[ticker] = fetch_filings_us(ticker, ticker_to_cik)
            elif market.upper() == "IL":
                filings[ticker] = fetch_filings_il(ticker)
            else:
                filings[ticker] = []
        except Exception as e:
            print(f"Filings fetch failed for {ticker}: {e}")
            filings[ticker] = []

    output = {
        "updated_at": datetime.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "performance": performance,
        "news": news,
        "filings": filings,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved updated data to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()#!/usr/bin/env python3
import json
import os
import time
import datetime
from datetime import timezone

import requests
import yfinance as yf
import feedparser

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "dashboard-data.json")

SEC_HEADERS = {
    "User-Agent": "stock-dashboard yovav81@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
NEWS_URL = "https://newsapi.org/v2/everything"
MAYA_RSS_URL = "https://maya.tase.co.il/rss/companyreports"

SEC_WANTED_FORMS = {
    "10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A",
    "13D", "13G", "SC 13D", "SC 13G", "F-1", "F-3", "424B2", "425"
}

IL_COMPANY_NAME_MAP = {
    "ELAL.TA": ["אל על", "אל-על", "אלעל"],
}


def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def safe_request_json(url, headers=None, params=None, timeout=30, allow_failure=False):
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Request failed: {url} -> {e}")
        if allow_failure:
            return None
        raise


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


def dedupe_articles(items):
    seen = set()
    result = []
    for item in items:
        key = (item.get("title"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def call_news_api(query):
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 10,
        "apiKey": NEWS_API_KEY,
    }

    try:
        resp = requests.get(NEWS_URL, params=params, timeout=30)
        print(f"News API query='{query}' status={resp.status_code}")
    except Exception as e:
        print(f"News request failed for query '{query}': {e}")
        return []

    if resp.status_code != 200:
        print(f"News API error for query '{query}': {resp.text[:300]}")
        return []

    try:
        payload = resp.json()
    except Exception as e:
        print(f"Could not parse news JSON for query '{query}': {e}")
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
    return articles


def fetch_news(company_name: str, ticker: str):
    if not NEWS_API_KEY:
        print(f"No NEWS_API_KEY set. Skipping news for {ticker}")
        return []

    queries = [
        f"\"{company_name}\" OR {ticker}",
        f"{company_name} stock",
        ticker,
    ]

    all_articles = []
    for query in queries:
        all_articles.extend(call_news_api(query))
        all_articles = dedupe_articles(all_articles)
        if len(all_articles) >= 3:
            break

    result = all_articles[:3]
    print(f"{ticker}: loaded {len(result)} news articles")
    return result


def fetch_sec_company_tickers():
    data = safe_request_json(
        SEC_TICKERS_URL,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    if not data:
        print("Failed to load SEC ticker map")
        return {}

    mapping = {}
    iterable = data.values() if isinstance(data, dict) else data

    for item in iterable:
        ticker = str(item.get("ticker", "")).upper().strip()
        cik_raw = item.get("cik_str")
        if not ticker or cik_raw is None:
            continue
        cik = str(cik_raw).zfill(10)
        mapping[ticker] = cik

    print(f"Loaded {len(mapping)} SEC ticker->CIK mappings")
    return mapping


def build_sec_filing_url(cik: str, accession_number: str, primary_document: str):
    if not cik or not accession_number or not primary_document:
        return None

    cik_no_leading = str(int(cik))
    accession_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading}/{accession_nodash}/{primary_document}"


def fetch_filings_us(ticker: str, ticker_to_cik: dict):
    cik = ticker_to_cik.get(ticker.upper())
    if not cik:
        print(f"No CIK found for {ticker}")
        return []

    url = SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)

    data = safe_request_json(
        url,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    time.sleep(0.2)

    if not data:
        print(f"No SEC submissions data for {ticker}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    fallback_filings = []

    for form, filing_date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
        filing_url = build_sec_filing_url(cik, accession, primary_doc)
        item = {
            "type": form,
            "date": filing_date,
            "title": form,
            "url": filing_url or f"https://www.sec.gov/edgar/search/#/entityName={ticker}",
        }

        if form in SEC_WANTED_FORMS:
            filings.append(item)
        else:
            fallback_filings.append(item)

        if len(filings) >= 3:
            break

    if len(filings) < 3:
        for item in fallback_filings:
            filings.append(item)
            if len(filings) >= 3:
                break

    result = filings[:3]
    print(f"{ticker}: loaded {len(result)} SEC filings")
    return result


def fetch_filings_il(ticker: str):
    company_names = IL_COMPANY_NAME_MAP.get(ticker)
    if not company_names:
        print(f"No MAYA company mapping found for {ticker}")
        return []

    try:
        feed = feedparser.parse(MAYA_RSS_URL)
    except Exception as e:
        print(f"MAYA RSS failed for {ticker}: {e}")
        return []

    filings = []

    for entry in getattr(feed, "entries", []):
        title = (getattr(entry, "title", "") or "").strip()

        if not any(name in title for name in company_names):
            continue

        published = getattr(entry, "published", "") or getattr(entry, "updated", "")
        date_str = published[:10] if published else ""

        filings.append(
            {
                "type": "MAYA",
                "date": date_str,
                "title": title,
                "url": getattr(entry, "link", ""),
            }
        )

        if len(filings) >= 3:
            break

    print(f"{ticker}: loaded {len(filings)} MAYA filings")
    return filings


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

        try:
            if market.upper() == "US":
                filings[ticker] = fetch_filings_us(ticker, ticker_to_cik)
            elif market.upper() == "IL":
                filings[ticker] = fetch_filings_il(ticker)
            else:
                filings[ticker] = []
        except Exception as e:
            print(f"Filings fetch failed for {ticker}: {e}")
            filings[ticker] = []

    output = {
        "updated_at": datetime.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "performance": performance,
        "news": news,
        "filings": filings,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved updated data to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()#!/usr/bin/env python3
import json
import os
import time
import datetime
from datetime import timezone

import requests
import yfinance as yf
import feedparser

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "dashboard-data.json")

SEC_HEADERS = {
    "User-Agent": "stock-dashboard yovav81@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
NEWS_URL = "https://newsapi.org/v2/everything"
MAYA_RSS_URL = "https://maya.tase.co.il/rss/companyreports"

SEC_WANTED_FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A"}

# מיפוי טיקר ישראלי -> שם חברה במאיה
IL_COMPANY_NAME_MAP = {
    "ELAL.TA": "אל על",
}


def get_date_ranges():
    today = datetime.datetime.utcnow().date()
    week_ago = today - datetime.timedelta(days=7)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return week_ago, month_start, year_start


def safe_request_json(url, headers=None, params=None, timeout=30, allow_failure=False):
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Request failed: {url} -> {e}")
        if allow_failure:
            return None
        raise


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

    params = {
        "q": f"\"{company_name}\" OR {ticker}",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 3,
        "apiKey": NEWS_API_KEY,
    }

    try:
        resp = requests.get(NEWS_URL, params=params, timeout=30)
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
    for article in payload.get("articles", [])[:3]:
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
    data = safe_request_json(
        SEC_TICKERS_URL,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    if not data:
        print("Failed to load SEC ticker map")
        return {}

    mapping = {}
    iterable = data.values() if isinstance(data, dict) else data

    for item in iterable:
        ticker = str(item.get("ticker", "")).upper().strip()
        cik_raw = item.get("cik_str")
        if not ticker or cik_raw is None:
            continue
        cik = str(cik_raw).zfill(10)
        mapping[ticker] = cik

    print(f"Loaded {len(mapping)} SEC ticker->CIK mappings")
    return mapping


def build_sec_filing_url(cik: str, accession_number: str, primary_document: str):
    if not cik or not accession_number or not primary_document:
        return None

    cik_no_leading = str(int(cik))
    accession_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading}/{accession_nodash}/{primary_document}"


def fetch_filings_us(ticker: str, ticker_to_cik: dict):
    cik = ticker_to_cik.get(ticker.upper())
    if not cik:
        print(f"No CIK found for {ticker}")
        return []

    url = SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)

    data = safe_request_json(
        url,
        headers=SEC_HEADERS,
        timeout=30,
        allow_failure=True,
    )

    time.sleep(0.2)

    if not data:
        print(f"No SEC submissions data for {ticker}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []

    for form, filing_date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
        if form not in SEC_WANTED_FORMS:
            continue

        filing_url = build_sec_filing_url(cik, accession, primary_doc)
        filings.append(
            {
                "type": form,
                "date": filing_date,
                "title": form,
                "url": filing_url or f"https://www.sec.gov/edgar/search/#/entityName={ticker}",
            }
        )

        if len(filings) >= 3:
            break

    print(f"{ticker}: loaded {len(filings)} SEC filings")
    return filings


def fetch_filings_il(ticker: str):
    company_name = IL_COMPANY_NAME_MAP.get(ticker)
    if not company_name:
        print(f"No MAYA company mapping found for {ticker}")
        return []

    try:
        feed = feedparser.parse(MAYA_RSS_URL)
    except Exception as e:
        print(f"MAYA RSS failed for {ticker}: {e}")
        return []

    filings = []

    for entry in getattr(feed, "entries", []):
        title = getattr(entry, "title", "") or ""
        if company_name not in title:
            continue

        published = getattr(entry, "published", "") or getattr(entry, "updated", "")
        date_str = published[:10] if published else ""

        filings.append(
            {
                "type": "MAYA",
                "date": date_str,
                "title": title,
                "url": getattr(entry, "link", ""),
            }
        )

        if len(filings) >= 3:
            break

    print(f"{ticker}: loaded {len(filings)} MAYA filings")
    return filings


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

        try:
            if market.upper() == "US":
                filings[ticker] = fetch_filings_us(ticker, ticker_to_cik)
            elif market.upper() == "IL":
                filings[ticker] = fetch_filings_il(ticker)
            else:
                filings[ticker] = []
        except Exception as e:
            print(f"Filings fetch failed for {ticker}: {e}")
            filings[ticker] = []

    output = {
        "updated_at": datetime.datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "performance": performance,
        "news": news,
        "filings": filings,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved updated data to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
