"""
BACKWARD-COMPATIBILITY SHIM
============================
Keeps the original import path working while the implementation lives in
`afl_agent.py`. Existing scripts can continue using `from afl_chat_agent import AFLChatAgent`.
"""

from afl_agent import AFLChatAgent

__all__ = ["AFLChatAgent"]
