#!/usr/bin/env python3
"""Proxy switch module for Clash (mihomo).

Two usage modes:
1. CLI: python3 proxy_switch.py [proxyon|proxyoff|switch|status]
2. Module: import and use in retry logic
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
import logging
from typing import Optional, Dict, List, Tuple, Set

logger = logging.getLogger(__name__)

CLASH_API = os.environ.get("CLASH_API", "http://127.0.0.1:9097")
PROXY_GROUP = os.environ.get("CLASH_PROXY_GROUP", "Proxy")
PROXY_HOST = "127.0.0.1"
PROXY_PORT = int(os.environ.get("CLASH_PROXY_PORT", "7897"))
DELAY_TEST_URL = "http://cp.cloudflare.com/generate_204"
DELAY_TIMEOUT = 3000  # ms


def _clash_request(path: str, method: str = "GET", data: dict | None = None) -> dict | None:
    """Send request to Clash API, bypassing proxy."""
    url = f"{CLASH_API}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")

    # Create opener that bypasses proxy
    handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    resp = opener.open(req, timeout=10)
    content = resp.read()
    if content:
        return json.loads(content)
    return None


def get_current_node() -> str:
    """Get the currently selected proxy node."""
    data = _clash_request(f"/proxies/{PROXY_GROUP}")
    return data.get("now", "unknown") if data else "unknown"


def get_all_nodes() -> list[str]:
    """Get all available nodes in the proxy group."""
    data = _clash_request(f"/proxies/{PROXY_GROUP}")
    if not data:
        return []
    return [n for n in data.get("all", []) if n not in ("DIRECT", "REJECT")]


def test_delays() -> dict[str, int]:
    """Test delay for all nodes. Returns {node_name: delay_ms}.

    Nodes that timeout get delay=99999.
    """
    path = f"/group/{PROXY_GROUP}/delay?timeout={DELAY_TIMEOUT}&url={DELAY_TEST_URL}"
    data = _clash_request(path)
    if not data:
        return {}
    return {name: (delay if isinstance(delay, int) and delay > 0 else 99999)
            for name, delay in data.items()}


def get_fastest_nodes(top_n: int = 3) -> list[tuple[str, int]]:
    """Test all nodes and return the fastest N as [(name, delay_ms), ...]."""
    delays = test_delays()
    sorted_nodes = sorted(delays.items(), key=lambda x: x[1])
    return [(name, delay) for name, delay in sorted_nodes[:top_n] if delay < 99999]


def switch_node(node_name: str) -> bool:
    """Switch proxy group to the specified node. Returns True on success."""
    try:
        _clash_request(f"/proxies/{PROXY_GROUP}", method="PUT", data={"name": node_name})
        logger.info("Switched to node: %s", node_name)
        return True
    except Exception:
        logger.exception("Failed to switch to node: %s", node_name)
        return False


def switch_to_fastest() -> str | None:
    """Test all nodes and switch to the fastest one. Returns node name or None."""
    fastest = get_fastest_nodes(1)
    if not fastest:
        logger.warning("No reachable nodes found")
        return None
    name, delay = fastest[0]
    if switch_node(name):
        logger.info("Switched to fastest node: %s (%dms)", name, delay)
        return name
    return None


def try_fastest_nodes(top_n: int = 3, test_url: str = "https://www.google.com") -> str | None:
    """Try top N fastest nodes until one actually works for HTTPS traffic.

    After switching each node, verifies connectivity through the proxy.
    Returns the working node name, or None if all fail.
    """
    fastest = get_fastest_nodes(top_n)
    if not fastest:
        logger.warning("No reachable nodes found")
        return None

    for name, delay in fastest:
        switch_node(name)
        # Verify actual connectivity through proxy
        try:
            proxy_handler = urllib.request.ProxyHandler({
                "http": f"http://{PROXY_HOST}:{PROXY_PORT}",
                "https": f"http://{PROXY_HOST}:{PROXY_PORT}",
            })
            opener = urllib.request.build_opener(proxy_handler)
            opener.open(test_url, timeout=5)
            logger.info("Node %s works (%dms delay)", name, delay)
            return name
        except Exception:
            logger.warning("Node %s (delay %dms) failed connectivity test", name, delay)
            continue

    logger.error("All top %d nodes failed connectivity test", top_n)
    return None


def proxy_env_vars() -> dict[str, str]:
    """Return env vars dict for enabling proxy."""
    proxy_url = f"http://{PROXY_HOST}:{PROXY_PORT}"
    return {
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
    }


def status() -> dict:
    """Return current proxy status."""
    current = get_current_node()
    return {
        "current_node": current,
        "proxy_url": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "clash_api": CLASH_API,
        "proxy_group": PROXY_GROUP,
    }


# --- Retry integration ---

class ProxySwitchRetry:
    """Drop-in retry helper that switches proxy node on timeout/connection errors.

    Usage:
        retry = ProxySwitchRetry(top_n=3)
        result = retry.call(some_function, arg1, arg2, kwarg1=val)
    """

    def __init__(self, top_n: int = 3, enable_proxy: bool = True):
        self._top_n = top_n
        self._enable_proxy = enable_proxy
        self._fastest: list[tuple[str, int]] | None = None
        self._tried: set[str] = set()

    def _ensure_proxy_env(self):
        if self._enable_proxy:
            os.environ.update(proxy_env_vars())

    def _get_next_node(self) -> str | None:
        if self._fastest is None:
            self._fastest = get_fastest_nodes(self._top_n)
        for name, _ in self._fastest:
            if name not in self._tried:
                self._tried.add(name)
                return name
        return None

    def call(self, func, *args, **kwargs):
        """Call func, retrying with different proxy nodes on network errors."""
        self._ensure_proxy_env()
        self._tried.clear()
        self._fastest = None

        # First attempt with current node
        try:
            return func(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning("Request failed (%s), trying fastest nodes...", e)

        # Switch nodes and retry
        while True:
            node = self._get_next_node()
            if node is None:
                raise ConnectionError(
                    f"All top {self._top_n} proxy nodes exhausted"
                )
            switch_node(node)
            try:
                return func(*args, **kwargs)
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.warning("Node %s failed (%s), trying next...", node, e)
                continue


# --- CLI ---

def _cli():
    usage = "Usage: proxy_switch.py [proxyon|proxyoff|switch|status|fastest]"
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "proxyon":
        env = proxy_env_vars()
        # Print shell export commands
        for k, v in env.items():
            print(f"export {k}={v}")
        print(f"# Current node: {get_current_node()}")

    elif cmd == "proxyoff":
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            print(f"unset {k}")

    elif cmd == "switch":
        node = try_fastest_nodes(top_n=3)
        if node:
            print(f"Switched to: {node}")
        else:
            print("ERROR: All nodes failed", file=sys.stderr)
            sys.exit(1)

    elif cmd == "fastest":
        nodes = get_fastest_nodes(5)
        for name, delay in nodes:
            print(f"{delay}ms\t{name}")

    elif cmd == "status":
        s = status()
        for k, v in s.items():
            print(f"{k}: {v}")

    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _cli()
