"""
Shared Kraken CLI wrapper — used by both KrakenDataAdapter and KrakenExecutor.

Kraken CLI supports two modes:
  1. Direct CLI:  kraken ticker BTCUSD -o json
  2. MCP stdio:   kraken mcp -s all --allow-dangerous
     (for agent-to-agent communication via Model Context Protocol)

This module uses direct CLI mode. For MCP stdio integration, configure
your MCP client with:
  {"command": "kraken", "args": ["mcp", "-s", "all", "--allow-dangerous"]}

Rate limits are handled automatically by the CLI with exponential backoff
and a global cooldown between consecutive calls.
"""
import json
import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# Exponential backoff for rate-limit retries
_MAX_RETRIES = 5
_BASE_DELAY = 3.0  # seconds (Kraken public API: 1 call/sec, burst penalty)

# Global rate limiter: minimum gap between any two CLI calls
_MIN_CALL_GAP = 2.0  # seconds
_last_call_time = 0.0
_call_lock = threading.Lock()


def _wait_for_rate_limit():
    """Enforce minimum gap between consecutive Kraken CLI calls."""
    global _last_call_time
    with _call_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_GAP:
            time.sleep(_MIN_CALL_GAP - elapsed)
        _last_call_time = time.monotonic()


def run_kraken(args: list[str], timeout: int = 30) -> dict:
    """Execute a kraken CLI command and return parsed JSON.

    Args:
        args: CLI arguments (e.g., ["ticker", "BTCUSD"])
        timeout: Command timeout in seconds

    Returns:
        Parsed JSON response, or empty dict on error

    Note:
        -o json is appended automatically to all commands.
        Rate-limit errors (exit code 2 / "rate limit") trigger automatic
        retry with exponential backoff up to 5 attempts.
        A global 2-second cooldown between calls prevents burst rate-limiting.
    """
    cmd = ["kraken"] + args + ["-o", "json"]

    for attempt in range(_MAX_RETRIES):
        _wait_for_rate_limit()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                # Rate-limit detection: retry with backoff
                # Only treat as rate-limit if the error message confirms it
                if "rate limit" in error_msg.lower():
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "kraken CLI rate-limited (attempt %d/%d), retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("kraken CLI error: %s", error_msg)
                return {}
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            logger.error("kraken CLI timeout: %s", " ".join(cmd))
            return {}
        except json.JSONDecodeError as e:
            logger.error("kraken CLI JSON parse error: %s", e)
            return {}

    logger.error("kraken CLI exhausted retries for: %s", " ".join(cmd))
    return {}
