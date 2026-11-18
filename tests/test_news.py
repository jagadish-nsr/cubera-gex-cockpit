"""Hand-checkable tests for the news-intensity signal (data/news.py).

Fixed RSS fixture with KNOWN pubDates and headlines, so count / impact / top are
reproducible by hand. No network — parse_feed and score_news are pure.
"""

from datetime import datetime, timezone

from data.news import is_market_mover, parse_feed, score_news

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>India-Pakistan border tensions: missile strike reported near LoC</title>
    <link>https://example.com/missile</link>
    <pubDate>Sat, 13 Jun 2026 10:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Nifty edges higher as banks lead the tape</title>
    <link>https://example.com/nifty</link>
    <pubDate>Sat, 13 Jun 2026 09:55:00 +0000</pubDate>
  </item>
  <item>
    <title>Four motorcyclists crashed in Kentucky waste spill; court upholds $11m award</title>
    <link>https://example.com/noise</link>
    <pubDate>Sat, 13 Jun 2026 09:50:00 +0000</pubDate>
  </item>
  <item>
    <title>Old market wrap from yesterday</title>
    <pubDate>Fri, 12 Jun 2026 10:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Undated explainer with no timestamp</title>
  </item>
</channel></rss>"""


def test_parse_feed_extracts_titles_dates_and_urls():
    items = parse_feed(FIXTURE)
    assert len(items) == 5
    assert items[0][1].startswith("India-Pakistan")
    assert items[0][0] == datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc)
    assert items[0][2] == "https://example.com/missile"
    assert items[4][0] is None            # missing pubDate -> None


def test_score_news_counts_fresh_movers_and_picks_top():
    now = datetime(2026, 6, 13, 10, 5, tzinfo=timezone.utc)  # 09:40-10:07 window
    s = score_news(parse_feed(FIXTURE), now, window_min=25)
    # Missile + Nifty are movers; Kentucky court award is noise (dropped).
    assert s["count"] == 2
    assert "missile" in s["top"].lower()
    assert s["impact"] >= 3
    assert all("Kentucky" not in h["title"] for h in s["headlines"])
    assert s["headlines"][0]["url"].startswith("http")
    assert s["headlines"][0]["is_fresh"] is True


def test_score_news_empty_when_all_stale():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)  # >25 min after all items
    s = score_news(parse_feed(FIXTURE), now, window_min=25)
    assert s["count"] == 0
    assert s["impact"] == 0
    assert s["top"] == ""
    assert isinstance(s.get("headlines"), list)


def test_is_market_mover_keeps_global_geo_trade_and_heavyweights():
    assert is_market_mover("Dow plunges over 850 points as Nvidia turmoil hits Wall Street")
    assert is_market_mover("Iran missile strike hits oil facilities")
    assert is_market_mover("US and India near trade deal on tariffs")
    assert is_market_mover("RBI rate hike bets rise after CPI print")
    assert is_market_mover("Reliance shares jump after crude eases")
    assert not is_market_mover(
        "Four motorcyclists crashed in Kentucky waste spill; court upholds $11m award"
    )
    assert not is_market_mover("Bollywood celebrity wedding lifestyle photos")
