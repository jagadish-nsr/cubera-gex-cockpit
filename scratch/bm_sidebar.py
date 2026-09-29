        st.checkbox("🐋 Big-money tape (live, auto)", value=True, key="simp_bigmoney",
                    help="Auto-run a background collector that streams NIFTY / BankNifty / SENSEX "
                         "current-expiry FUTURES ticks and flags big prints & bursts on the tape below. "
                         "Starts with the dashboard; needs a Dhan token. It does NOT save raw ticks — "
                         "the raw archive is owned by the scheduled headless collector, so the two never "
                         "double-write. SENSEX needs the BSE live-data entitlement — 0 ticks without it.")
        with st.expander("🎬 Replay big-money (from raw ticks)", expanded=False):
            from app.big_money_live import available_raw_days
            _rd = available_raw_days(symbol)
            if not _rd:
                st.caption(f"No captured raw ticks for {symbol} yet — run the collector a full session.")
            else:
                st.checkbox(f"Replay {symbol}'s captured tape", value=False, key="bm_replay_on",
                            help="Scrub through a captured day's big-money tape, re-derived tick-by-tick "
                                 "from the raw archive (the source of truth) up to the chosen clock.")
                st.selectbox("Day", _rd[::-1], key="bm_replay_day")
                _times = [f"{m // 60:02d}:{m % 60:02d}" for m in range(9 * 60 + 15, 15 * 60 + 31, 5)]
                st.select_slider("Clock (IST)", options=_times, value=_times[-1], key="bm_replay_clock")
        st.divider()
