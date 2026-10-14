from urbanbot import backtest, decide, mentions, momentum_signal, sentiment


def test_sentiment_and_negation():
    assert sentiment("NVDA earnings beat, stock surges to a record") > 0.5
    assert sentiment("massive plunge after the fraud probe") < -0.5
    assert sentiment("this is not bullish") < 0
    assert sentiment("the meeting is on tuesday") == 0


def test_mentions_needs_word_boundary():
    assert mentions("buying $TSLA today", "TSLA")
    assert not mentions("NVDAX fund", "NVDA")


def test_momentum_and_backtest():
    up = [100 + i for i in range(120)]
    assert momentum_signal(up) == 1
    assert momentum_signal(up[::-1]) == 0
    r = backtest(up[::-1] + up)  # down then up: strategy sits out the fall
    assert r["strategy"]["max_drawdown"] > r["buy_and_hold"]["max_drawdown"]


def test_sentiment_veto():
    assert decide(1, {"mean": 0.1}) == "hold-long"
    assert decide(1, {"mean": -0.4}) == "flat"
    assert decide(0, {"mean": 0.9}) == "flat"
