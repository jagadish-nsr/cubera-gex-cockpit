"""Paper / Confirm execution path for CryptoCubera.

Never places live capital. Delta demo/testnet only when credentials are set;
otherwise a local sim adapter fills the bracket into the trade ledger.
"""

from crypto.exec.service import ExecutionService, get_execution_service

__all__ = ["ExecutionService", "get_execution_service"]
