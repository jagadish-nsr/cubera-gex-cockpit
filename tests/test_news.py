"""Hand-checkable tests for the news-intensity signal (data/news.py).

Fixed RSS fixture with KNOWN pubDates and headlines, so count / impact / top are
reproducible by hand. No network — parse_feed and score_news are pure.
"""

from datetime import datetime, timezone

from data.news import parse_feed, score_news

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>India-Pakistan border tensions: missile strike reported near LoC</title>
    <pubDate>Sat, 13 Jun 2026 10:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Nifty edges higher as banks lead the tape</title>
    <pubDate>Sat, 13 Jun 2026 09:55:00 +0000</pubDate>
  </item>
  <item>
    <title>Old market wrap from yesterday</title>
    <pubDate>Fri, 12 Jun 2026 10:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Undated explainer with no timestamp</title>
  </item>
</channel></rss>"""


def test_parse_feed_extracts_titles_and_dates():
    items = parse_feed(FIXTURE)
    assert len(items) == 4
    assert items[0][1].startswith("India-Pakistan")
    assert items[0][0] == datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc)
    assert items[3][0] is None            # missing pubDate -> None


def test_score_news_counts_fresh_and_picks_top():
    now = datetime(2026, 6, 13, 10, 5, tzinfo=timezone.utc)  # 09:40-10:07 window
    s = score_news(parse_feed(FIXTURE), now, window_min=25)
    # Only the missile (10:00) and the Nifty headline (09:55) are fresh; the
    # yesterday item and the undated item are excluded.
    assert s["count"] == 2
    # missile(3) + strike(2) + border(2) dominate -> that is the top headline.
    assert "missile" in s["top"].lower()
    assert s["impact"] >= 7
    assert "headlines" in s
    assert len(s["headlines"]) >= 2
    top_hl = s["headlines"][0]
    assert top_hl["category"] == "Geopolitics"
    assert top_hl["is_fresh"] is True


def test_score_news_empty_when_all_stale():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)  # >25 min after all items
    s = score_news(parse_feed(FIXTURE), now, window_min=25)
    assert s["count"] == 0
    assert s["impact"] == 0
    assert s["top"] == ""
    # Headlines are still accessible for market context
    assert isinstance(s.get("headlines"), list)

