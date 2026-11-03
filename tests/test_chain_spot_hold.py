"""A chain spot that does not change is not a live print."""

from app.live_fetcher import chain_spot_hold


def test_a_new_print_is_not_stale():
    record, stale, held = chain_spot_hold(None, 23331.1, 1_000.0)
    assert record == (23331.1, 1_000.0)
    assert stale is False
    assert held == 0.0


def test_the_same_print_is_stale_at_three_minutes():
    previous = (23331.1, 1_000.0)
    record, stale, held = chain_spot_hold(previous, 23331.12, 1_179.0)
    assert stale is False
    assert held == 179.0
    record, stale, held = chain_spot_hold(record, 23331.1, 1_180.0)
    assert stale is True
    assert held == 180.0
    assert record[1] == 1_000.0


def test_a_later_print_resets_the_hold():
    previous = (23331.1, 1_000.0)
    record, stale, held = chain_spot_hold(previous, 23440.8, 1_900.0)
    assert stale is False
    assert held == 0.0
    assert record == (23440.8, 1_900.0)
