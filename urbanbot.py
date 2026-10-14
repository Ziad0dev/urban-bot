#!/usr/bin/env python3
"""urban-bot: social-sentiment scraper + paper-trading bot.

    python urbanbot.py sentiment NVDA TSLA          # scrape posts, score sentiment per ticker
    python urbanbot.py backtest NVDA --years 3      # momentum strategy vs buy & hold
    python urbanbot.py trade NVDA TSLA AAPL         # one paper-trading step (momentum + sentiment)
    python urbanbot.py portfolio                    # show the paper portfolio

Sources:
  * Hacker News (Algolia API): always on, no key
  * Reddit (r/wallstreetbets, r/stocks, ...): set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET
    from a "script" app at https://www.reddit.com/prefs/apps (Reddit blocks anonymous API use)
  * X/Twitter: set X_BEARER_TOKEN (recent search needs a paid API tier)
Prices: Yahoo Finance chart endpoint (unofficial, daily closes).

PAPER TRADING ONLY. Nothing here talks to a broker. Not financial advice.
"""
import argparse
import base64
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
PORTFOLIO = ROOT / "data" / "portfolio.json"
UA = "urban-bot/0.1 (research prototype)"

# ------------------------------------------------------------- sentiment

# Small finance-flavoured lexicon (weights -3..3); retail slang included on purpose
LEXICON = {
    "bullish": 3, "moon": 2, "mooning": 3, "rocket": 2, "calls": 1, "buy": 1, "buying": 1, "long": 1,
    "beat": 2, "beats": 2, "upgrade": 2, "upgraded": 2, "surge": 2, "surges": 2, "soar": 2, "soars": 2,
    "rally": 2, "record": 1, "strong": 1, "growth": 1, "profit": 1, "undervalued": 2, "breakout": 2,
    "outperform": 2, "tendies": 2, "gains": 2, "green": 1, "squeeze": 1, "win": 1, "winning": 1,
    "bearish": -3, "puts": -1, "sell": -1, "selling": -1, "short": -1, "miss": -2, "misses": -2,
    "downgrade": -2, "downgraded": -2, "plunge": -3, "plunges": -3, "crash": -3, "crashes": -3,
    "dump": -2, "dumping": -2, "overvalued": -2, "bubble": -2, "lawsuit": -2, "probe": -1, "fraud": -3,
    "loss": -2, "losses": -2, "red": -1, "weak": -1, "layoffs": -2, "recall": -2, "bagholder": -2,
    "bankrupt": -3, "bankruptcy": -3, "fall": -1, "falls": -1, "drop": -1, "drops": -1, "risk": -1,
}
NEGATORS = {"not", "no", "never", "don't", "doesn't", "isn't", "wasn't", "won't", "can't"}


def sentiment(text):
    """VADER-style score in [-1, 1]: lexicon sum with negation, normalized."""
    words = re.findall(r"[a-z']+", text.lower())
    total = 0.0
    for i, w in enumerate(words):
        if w in LEXICON:
            s = LEXICON[w]
            if any(p in NEGATORS for p in words[max(0, i - 3):i]):
                s = -0.7 * s
            total += s
    return total / math.sqrt(total * total + 15) if total else 0.0


# ------------------------------------------------------------- scrapers

def get_json(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def mentions(text, ticker):
    return re.search(rf"(\${ticker}\b|\b{ticker}\b)", text) is not None


def hackernews(ticker, limit=50):
    hits = get_json(f"https://hn.algolia.com/api/v1/search_by_date?query={ticker}&tags=(story,comment)&hitsPerPage={limit}")["hits"]
    posts = []
    for h in hits:
        text = h.get("title") or h.get("comment_text") or ""
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        if mentions(text, ticker):
            posts.append({"source": "hackernews", "text": text[:500], "created": h["created_at"]})
    return posts


def reddit(ticker, subreddits=("wallstreetbets", "stocks", "investing"), limit=50):
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return []
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    token = get_json("https://www.reddit.com/api/v1/access_token", {"Authorization": f"Basic {auth}"},
                     b"grant_type=client_credentials")["access_token"]
    posts = []
    for sub in subreddits:
        q = urllib.parse.urlencode({"q": ticker, "restrict_sr": 1, "sort": "new", "limit": limit})
        data = get_json(f"https://oauth.reddit.com/r/{sub}/search?{q}", {"Authorization": f"Bearer {token}"})
        for child in data["data"]["children"]:
            d = child["data"]
            text = f"{d['title']} {d.get('selftext', '')}"
            if mentions(text, ticker):
                posts.append({"source": f"r/{sub}", "text": text[:500],
                              "created": datetime.fromtimestamp(d["created_utc"], timezone.utc).isoformat()})
    return posts


def twitter(ticker, limit=50):
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return []
    q = urllib.parse.urlencode({"query": f"${ticker} lang:en -is:retweet", "max_results": min(100, max(10, limit)),
                                "tweet.fields": "created_at"})
    data = get_json(f"https://api.x.com/2/tweets/search/recent?{q}", {"Authorization": f"Bearer {token}"})
    return [{"source": "x", "text": t["text"], "created": t.get("created_at", "")} for t in data.get("data", [])]


def scrape(ticker):
    posts = []
    for source in (hackernews, reddit, twitter):
        try:
            posts += source(ticker)
        except Exception as e:  # one dead source shouldn't stop the others
            print(f"  ({source.__name__} failed: {e})", file=sys.stderr)
    for p in posts:
        p["score"] = sentiment(p["text"])
    return posts


def ticker_sentiment(ticker):
    posts = scrape(ticker)
    scored = [p["score"] for p in posts]
    mean = sum(scored) / len(scored) if scored else 0.0
    return {"ticker": ticker, "posts": len(posts), "mean": mean,
            "bullish": sum(s > 0.05 for s in scored), "bearish": sum(s < -0.05 for s in scored), "sample": posts[:3]}


# ------------------------------------------------------------- prices & strategy

def closes(ticker, years=2):
    data = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={years}y&interval=1d",
                    {"User-Agent": "Mozilla/5.0"})
    r = data["chart"]["result"][0]
    pairs = [(t, c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    return [datetime.fromtimestamp(t, timezone.utc).date().isoformat() for t, _ in pairs], [c for _, c in pairs]


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def momentum_signal(prices, fast=20, slow=50):
    """+1 when the fast moving average is above the slow one, else 0 (long/flat)."""
    f, s = sma(prices, fast), sma(prices, slow)
    return 1 if f is not None and s is not None and f > s else 0


def backtest(prices, fast=20, slow=50, cost=0.001):
    """Daily long/flat momentum; the signal from day t is traded at day t's close and earns day t+1."""
    equity, hold, position, trades = [1.0], [1.0], 0, 0
    for t in range(1, len(prices)):
        r = prices[t] / prices[t - 1] - 1
        equity.append(equity[-1] * (1 + position * r))
        hold.append(hold[-1] * (1 + r))
        new = momentum_signal(prices[:t + 1], fast, slow)
        if new != position:
            equity[-1] *= 1 - cost
            trades += 1
        position = new

    def stats(curve):
        rets = [b / a - 1 for a, b in zip(curve, curve[1:])]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets)) or 1e-12
        peak, dd = curve[0], 0.0
        for v in curve:
            peak = max(peak, v)
            dd = min(dd, v / peak - 1)
        return {"return": curve[-1] - 1, "sharpe": mu / sd * math.sqrt(252), "max_drawdown": dd}

    return {"strategy": stats(equity), "buy_and_hold": stats(hold), "trades": trades}


# ------------------------------------------------------------- paper portfolio

def load_portfolio():
    if PORTFOLIO.exists():
        return json.loads(PORTFOLIO.read_text())
    return {"cash": 100_000.0, "positions": {}, "log": []}


def save_portfolio(p):
    PORTFOLIO.parent.mkdir(exist_ok=True)
    PORTFOLIO.write_text(json.dumps(p, indent=1))


def decide(momentum, sent):
    """Momentum sets the direction; sentiment only vetoes buys when the crowd turns clearly negative."""
    if momentum and sent["mean"] > -0.15:
        return "hold-long"
    return "flat"


def cmd_trade(args):
    p = load_portfolio()
    targets = {}
    for ticker in args.tickers:
        _, prices = closes(ticker, 1)
        sent = ticker_sentiment(ticker)
        action = decide(momentum_signal(prices), sent)
        targets[ticker] = (action, prices[-1])
        print(f"{ticker:6s} price {prices[-1]:9.2f}  momentum {'up' if momentum_signal(prices) else 'down'}  "
              f"sentiment {sent['mean']:+.2f} ({sent['posts']} posts)  -> {action}")
    # equal-weight the longs, sell everything else
    value = p["cash"] + sum(q * targets.get(t, (None, 0))[1] for t, q in p["positions"].items() if t in targets)
    longs = [t for t, (a, _) in targets.items() if a == "hold-long"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ticker, (action, price) in targets.items():
        want = (value / len(longs) / price) if ticker in longs else 0.0
        have = p["positions"].get(ticker, 0.0)
        delta = want - have
        if abs(delta * price) < 1:
            continue
        p["cash"] -= delta * price
        p["positions"][ticker] = want
        p["log"].append({"time": now, "ticker": ticker, "shares": round(delta, 4), "price": price})
        print(f"  paper {'BUY' if delta > 0 else 'SELL'} {abs(delta):.2f} {ticker} @ {price:.2f}")
    p["positions"] = {t: q for t, q in p["positions"].items() if q > 1e-9}
    save_portfolio(p)
    print(f"portfolio value ≈ {value:,.2f} (cash {p['cash']:,.2f})")


def cmd_sentiment(args):
    for ticker in args.tickers:
        s = ticker_sentiment(ticker)
        print(f"{ticker}: {s['posts']} posts, mean {s['mean']:+.2f}, {s['bullish']} bullish / {s['bearish']} bearish")
        for post in s["sample"]:
            print(f"   [{post['source']}] {post['score']:+.2f}  {post['text'][:100]}")


def cmd_backtest(args):
    for ticker in args.tickers:
        dates, prices = closes(ticker, args.years)
        r = backtest(prices, args.fast, args.slow)
        s, h = r["strategy"], r["buy_and_hold"]
        print(f"{ticker} {dates[0]}..{dates[-1]}  SMA{args.fast}/{args.slow}, {r['trades']} trades")
        print(f"  strategy:     return {s['return']:+.1%}  sharpe {s['sharpe']:.2f}  max drawdown {s['max_drawdown']:.1%}")
        print(f"  buy & hold:   return {h['return']:+.1%}  sharpe {h['sharpe']:.2f}  max drawdown {h['max_drawdown']:.1%}")


def cmd_portfolio(args):
    p = load_portfolio()
    print(f"cash {p['cash']:,.2f}")
    for t, q in p["positions"].items():
        print(f"  {t}: {q:.4f} shares")
    for entry in p["log"][-10:]:
        print(f"  {entry['time']}  {entry['ticker']} {entry['shares']:+.4f} @ {entry['price']:.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("sentiment", "trade"):
        sub.add_parser(name).add_argument("tickers", nargs="+", type=str.upper)
    p = sub.add_parser("backtest")
    p.add_argument("tickers", nargs="+", type=str.upper)
    p.add_argument("--years", type=int, default=3)
    p.add_argument("--fast", type=int, default=20)
    p.add_argument("--slow", type=int, default=50)
    sub.add_parser("portfolio")
    args = parser.parse_args()
    {"sentiment": cmd_sentiment, "trade": cmd_trade, "backtest": cmd_backtest, "portfolio": cmd_portfolio}[args.cmd](args)


if __name__ == "__main__":
    main()
