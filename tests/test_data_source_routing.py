"""Tests for config routing mechanics."""
from __future__ import annotations
import config

class TestDhanCredentialConstants:
    """Verify DHAN credential constants exist in config."""
    def test_dhan_client_id_constant_exists(self):
        assert hasattr(config, "DHAN_CLIENT_ID")
    def test_dhan_access_token_constant_exists(self):
        assert hasattr(config, "DHAN_ACCESS_TOKEN")
