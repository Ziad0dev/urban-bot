# urban-bot
trading bot and twitter/reddit scraper

> **Paper trading only.** Nothing connects to a broker; this is not financial advice.

`urbanbot.py`:
- **Scraper:** collects posts mentioning a ticker from Hacker News (keyless), Reddit (r/wallstreetbets, r/stocks, r/investing; set `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` from a "script" app, since Reddit blocks anonymous API access) and X (`X_BEARER_TOKEN`, paid tier for search)
- **Sentiment:** a VADER-style lexicon with finance/retail slang (moon, tendies, bagholder, ...) and negation handling
- **Strategy:** SMA20/50 momentum (long/flat) where sentiment can veto a buy when the crowd turns clearly negative
- **Backtest:** momentum vs buy & hold with costs, Sharpe and max drawdown (prices from Yahoo's chart endpoint)
- **Paper portfolio:** `trade` rebalances an equal-weight paper portfolio in `data/portfolio.json`

```sh
python3 urbanbot.py sentiment NVDA TSLA
python3 urbanbot.py backtest NVDA SPY --years 3
python3 urbanbot.py trade NVDA TSLA AAPL
python3 urbanbot.py portfolio
python3 -m pytest tests
```

What the backtest says (3 years to Oct 2026): momentum cut max drawdown roughly in half on NVDA and SPY but returned less than buy & hold in a bull market. Sentiment isn't backtested because historical social data isn't freely available.
