---
name: proxy-switch
description: Manage network proxy and switch Clash nodes. Use when encountering network timeouts, connection refused, DNS failures, or any connectivity issues in shell commands or Python code. Also use when the user asks to enable/disable proxy or switch proxy nodes.
---

# Proxy Switch

Terminal proxy management and Clash node switching for network connectivity issues.

## Quick Reference

| Action                      | Command                                                        |
| --------------------------- | -------------------------------------------------------------- |
| Enable proxy                | `source <(python3 SKILL_DIR/scripts/proxy_switch.py proxyon)`  |
| Disable proxy               | `source <(python3 SKILL_DIR/scripts/proxy_switch.py proxyoff)` |
| Check status                | `python3 SKILL_DIR/scripts/proxy_switch.py status`             |
| Show fastest nodes          | `python3 SKILL_DIR/scripts/proxy_switch.py fastest`            |
| Auto-switch to working node | `python3 SKILL_DIR/scripts/proxy_switch.py switch`             |

Replace `SKILL_DIR` with the actual path to this skill directory.

## Workflow

```
Network error?
  → Step 1: Enable proxy (proxyon)
  → Still failing?
    → Step 2: Run `switch` to test nodes and pick fastest working one
  → Still failing?
    → Check if Clash is running, or if the target is unreachable regardless of proxy
```

### Step 1: Enable Proxy in Shell

Run in the current shell to set `http_proxy` / `https_proxy`:

```bash
source <(python3 SKILL_DIR/scripts/proxy_switch.py proxyon)
```

This sets proxy to `http://127.0.0.1:7897`. Verify with:

```bash
curl -I https://www.google.com
```

### Step 2: Switch to Fastest Working Node

If proxy is on but connections still fail (current node may be down):

```bash
python3 SKILL_DIR/scripts/proxy_switch.py switch
```

This will:

1. Test delay on all nodes in the Clash proxy group
2. Pick the top 3 fastest
3. Try each one with an actual HTTPS connectivity test
4. Switch to the first one that works

## Python Module Usage

For integrating into retry logic (e.g., wrapping API calls that may timeout):

```python
from proxy_switch import ProxySwitchRetry

retry = ProxySwitchRetry(top_n=3)
result = retry.call(requests.get, "https://api.example.com/data", timeout=10)
```

`ProxySwitchRetry.call()` will:

1. Try the request with current proxy settings
2. On `ConnectionError` / `TimeoutError` / `OSError`: test all nodes, pick top 3 fastest
3. Switch to each and retry until one works
4. Raise `ConnectionError` if all nodes exhausted

### Other useful functions

```python
from proxy_switch import (
    get_current_node,     # -> "台湾 B - 高级节点 | ..."
    get_fastest_nodes,    # -> [("香港 E - ...", 45), ...]
    switch_node,          # switch_node("香港 E - ...") -> True
    try_fastest_nodes,    # try top N with connectivity test -> "香港 E - ..."
    proxy_env_vars,       # -> {"http_proxy": "http://127.0.0.1:7897", ...}
)
```

## Configuration

Environment variables (all optional):

| Variable            | Default                 | Description                 |
| ------------------- | ----------------------- | --------------------------- |
| `CLASH_API`         | `http://127.0.0.1:9097` | Clash (mihomo) API endpoint |
| `CLASH_PROXY_GROUP` | `Proxy`                 | Proxy group name to manage  |
| `CLASH_PROXY_PORT`  | `7897`                  | Local proxy port            |
