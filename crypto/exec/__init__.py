"""Paper / Live Confirm execution path for CryptoCubera.

Advisory is default. Paper Confirm uses Delta demo/testnet or local sim.
Live Confirm is flag-gated (``exec.live`` + ``exec.confirm``) and fail-closed
without explicit live keys, ack, and IP-whitelist ack. Auto is refused.
"""
