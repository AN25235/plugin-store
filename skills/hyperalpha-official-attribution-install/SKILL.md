---
name: hyperalpha-official-attribution-install
description: One-shot setup guide — install Agentic Wallet, Hyperliquid Plugin, and HyperAlpha Adaptive strategy for AI-powered on-chain perpetual trading.
version: 2.0.0
author: AN25235
license: MIT
tags:
  - hyperalpha-adaptive
  - agentic-wallet
  - hyperliquid-plugin
  - onchainos
  - installer
---

# HyperAlpha Adaptive — AI 链上交易一键部署

为你的 AI Agent 接入完整的链上永续合约执行能力。私钥在可信执行环境（TEE）内生成与签名，交易前自动风控检测，从信号到成交，全程闭环。

## When to use

Use this skill when the user wants to:
- set up an AI Agent for on-chain perpetual trading on Hyperliquid
- install the full stack: Agentic Wallet → Hyperliquid Plugin → HyperAlpha Adaptive strategy
- get from zero to a working scan/evaluate/trade flow in one session

## Overview

The full stack has three layers:

1. **Agentic Wallet (onchainos)** — TEE-secured wallet, handles signing and risk checks
2. **Hyperliquid Plugin** — on-chain perp execution (order, close, tpsl, positions)
3. **HyperAlpha Adaptive** — multi-asset adaptive strategy (scan, evaluate, signal scoring)

## Required steps

### Step 1: Install Onchain OS (onchainos CLI)

```bash
onchainos --version 2>/dev/null || curl -fsSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
```

Then install the onchainos skills for AI agent integration:

```bash
npx skills add okx/onchainos-skills --yes --global
```

### Step 2: Create Agentic Wallet

This is an interactive step. The AI agent should guide the user through it.

**2a. Start login with email:**

```bash
onchainos wallet login <user-email>
```

The system sends an OTP code to the email.

**2b. Verify OTP:**

```bash
onchainos wallet verify <otp-code>
```

On first login, the wallet is created automatically:
- EVM address is generated
- Solana address is generated
- Private key is generated and stored inside the TEE — never exposed to the agent or user

On subsequent logins with the same email, the existing wallet is restored.

**2c. Verify wallet is ready:**

```bash
onchainos wallet addresses
```

Should return `"ok": true` with addresses listed.

**Important:** If this step fails or the user cannot provide an email/OTP, stop here. The rest of the flow depends on a working wallet.

### Step 3: Install Plugin Store and Hyperliquid Plugin

```bash
npx skills add okx/plugin-store --skill plugin-store --yes --global
npx skills add okx/plugin-store --skill hyperliquid-plugin --yes --global
```

Then install the binary and launcher:

```bash
# Install shared launcher
LAUNCHER="$HOME/.plugin-store/launcher.sh"
if [ ! -f "$LAUNCHER" ]; then
  mkdir -p "$HOME/.plugin-store"
  curl -fsSL "https://raw.githubusercontent.com/okx/plugin-store/main/scripts/launcher.sh" -o "$LAUNCHER"
  chmod +x "$LAUNCHER"
fi

# Download binary
OS=$(uname -s | tr A-Z a-z)
ARCH=$(uname -m)
case "${OS}_${ARCH}" in
  darwin_arm64)  TARGET="aarch64-apple-darwin" ;;
  darwin_x86_64) TARGET="x86_64-apple-darwin" ;;
  linux_x86_64)  TARGET="x86_64-unknown-linux-musl" ;;
  linux_aarch64) TARGET="aarch64-unknown-linux-musl" ;;
esac
mkdir -p ~/.local/bin
curl -fsSL "https://github.com/okx/plugin-store/releases/download/plugins/hyperliquid-plugin@0.3.9/hyperliquid-plugin-${TARGET}" -o ~/.local/bin/.hyperliquid-plugin-core
chmod +x ~/.local/bin/.hyperliquid-plugin-core
ln -sf "$LAUNCHER" ~/.local/bin/hyperliquid-plugin
mkdir -p "$HOME/.plugin-store/managed"
echo "0.3.9" > "$HOME/.plugin-store/managed/hyperliquid-plugin"
```

Verify:

```bash
export PATH="$HOME/.local/bin:$PATH"
hyperliquid-plugin --version
```

### Step 4: Register Hyperliquid signing address

```bash
hyperliquid-plugin register
```

If `"status": "ready"`, proceed. If `"status": "setup_required"`, follow the instructions (deposit USDC to the signing address, or register as API wallet via Hyperliquid web UI).

### Step 5: Check wallet and exchange status

```bash
hyperliquid-plugin quickstart
```

This shows: Arbitrum USDC balance, Hyperliquid account value, open positions, and a recommended next action.

### Step 6: Install HyperAlpha Adaptive strategy

The strategy code is bundled inside this skill's `strategy-bundle/` directory. Copy it to `~/hyperalpha-adaptive`:

```bash
# Find where this skill was installed
SKILL_DIR=""
for d in "$HOME/.agents/skills" "$HOME/.claude" "$HOME/.cursor" "$HOME/.codex"; do
  if [ -d "$d/hyperalpha-official-attribution-install/strategy-bundle" ]; then
    SKILL_DIR="$d/hyperalpha-official-attribution-install/strategy-bundle"
    break
  fi
done

# Fallback: search common agent skill paths
if [ -z "$SKILL_DIR" ]; then
  SKILL_DIR=$(find "$HOME" -maxdepth 5 -path "*/hyperalpha-official-attribution-install/strategy-bundle" -type d 2>/dev/null | head -1)
fi

if [ -z "$SKILL_DIR" ]; then
  echo "ERROR: strategy-bundle not found. Re-run: npx skills add AN25235/plugin-store --skill hyperalpha-official-attribution-install --yes --global"
  exit 1
fi

echo "Found strategy bundle: $SKILL_DIR"
rm -rf ~/hyperalpha-adaptive
cp -r "$SKILL_DIR" ~/hyperalpha-adaptive
echo "Installed to: ~/hyperalpha-adaptive"
```

### Step 7: Run dry-run verification

```bash
cd ~/hyperalpha-adaptive
python3 -m py_compile scripts/*.py
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json
```

### Step 8: Create the one-click launcher

Create `~/hyperalpha-adaptive/hyperalpha_official_path.sh`:

```bash
cat > ~/hyperalpha-adaptive/hyperalpha_official_path.sh << 'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

ROOT_DIR="$HOME/hyperalpha-adaptive"
MODE="${1:-scan}"
shift || true

cd "$ROOT_DIR"

case "$MODE" in
  scan)
    python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json --fetch-market "$@"
    ;;
  evaluate)
    python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json --fetch-market "$@"
    ;;
  *)
    echo "Usage: ./hyperalpha_official_path.sh [scan|evaluate]" >&2
    exit 1
    ;;
esac

echo
echo "To execute a trade from the output above, copy the hyperliquid-plugin command template and run it with --confirm."
LAUNCHER
chmod +x ~/hyperalpha-adaptive/hyperalpha_official_path.sh
```

## Usage after installation

### Scan for opportunities
```bash
cd ~/hyperalpha-adaptive && ./hyperalpha_official_path.sh scan
```

### Evaluate a single market
```bash
cd ~/hyperalpha-adaptive && ./hyperalpha_official_path.sh evaluate
```

### Execute a trade

From the scan/evaluate output, copy the `hyperliquid_command_template` field, e.g.:

```bash
hyperliquid-plugin order --coin BTC --side long --size 193.17 --strategy-id an25hlq1 --confirm
```

### Check positions
```bash
hyperliquid-plugin positions
```

### Set stop-loss / take-profit
```bash
hyperliquid-plugin tpsl --coin BTC --sl-px 95000 --tp-px 110000 --confirm
```

## Success criteria

A complete installation should produce:
1. `onchainos wallet addresses` returns `"ok": true`
2. `hyperliquid-plugin --version` returns version number
3. `hyperliquid-plugin quickstart` returns account status
4. All 4 dry-run checks pass
5. `~/hyperalpha-adaptive/hyperalpha_official_path.sh` exists and is executable
6. Next command: `cd ~/hyperalpha-adaptive && ./hyperalpha_official_path.sh scan`

## Pitfalls learned from real installations

1. **`onchainos login` does NOT exist.** The correct command is `onchainos wallet login <email>`. The top-level `onchainos` CLI has no `login` subcommand — wallet operations are under the `wallet` subcommand. If a user reports `error: unrecognized subcommand 'login'`, this is the cause.
2. **`npx skills add` without `--yes` hangs** in non-interactive terminals (agent environments). Always pass `--yes --global` to avoid the interactive agent-selection prompt.
3. **`~/.local/bin` is not in PATH by default** on most Ubuntu/Debian systems. Every command that uses `hyperliquid-plugin` or `onchainos` must be preceded by `export PATH="$HOME/.local/bin:$PATH"`, or the agent should set it once at the start of the session. Without this, `hyperliquid-plugin: command not found` is the most common failure.
4. **After `quickstart` returns `"status": "no_funds"`**, the user must: (a) send USDC to their Arbitrum wallet address shown in `onchainos wallet addresses`, (b) run `hyperliquid-plugin quickstart` again to confirm the balance, (c) run `hyperliquid-plugin deposit --amount <amount> --confirm` to move funds from Arbitrum into Hyperliquid. Minimum deposit is $5.
5. **Do not paste the multi-line instructions with arrows (↓) into the terminal.** If giving the user copy-paste commands, give them one command at a time, not a flow diagram. The user in the first real test pasted the entire instruction block including Chinese text and arrows, causing `command not found` errors for every non-command line.

## Error handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `unrecognized subcommand 'login'` | Used `onchainos login` instead of `onchainos wallet login` | Use `onchainos wallet login <email>` |
| `onchainos wallet login` fails | Email/network issue | Retry, check email spelling |
| `not logged in` | Wallet session expired or never completed | Re-run `onchainos wallet login <email>` + `onchainos wallet verify <otp>` |
| `WALLET_NOT_FOUND` | Wallet not created | Complete Step 2 (login + verify) first |
| `setup_required` from register | Signing address needs setup | Deposit USDC to the signing address, or register as API wallet via Hyperliquid web UI |
| `no_funds` from quickstart | No USDC on Arbitrum or Hyperliquid | Send USDC to the EVM address on Arbitrum, then `hyperliquid-plugin deposit --amount <N> --confirm` |
| `hyperliquid-plugin: command not found` | Binary not in PATH | Run `export PATH="$HOME/.local/bin:$PATH"` before every command, or add it to `~/.bashrc` |
| `npx skills add` hangs | Interactive prompt waiting for input | Always use `--yes --global` flags |
| `strategy-bundle not found` | Skill installed but strategy code not bundled | Re-run `npx skills add AN25235/plugin-store --skill hyperalpha-official-attribution-install --yes --global` to get the latest version with bundled code |
| scan returns only HYPE, BTC/ETH/SOL missing | `positions` passed as list `[]` instead of dict `{}` in scan input | The `--fetch-market` scan path validates `positions` as a dict (coin→position object). If the auto-trade daemon builds `positions_for_input` as a list, the validation error silently discards all successfully-fetched coin results. Fix: use `positions_for_input = {}` and `positions_for_input[coin] = {...}` |
## Copy-paste instruction for another AI

```text
帮我安装 HyperAlpha Adaptive 链上交易全套环境。按以下顺序执行：1) 安装 onchainos CLI 和 skills；2) 用我的邮箱登录 Agentic Wallet（onchainos wallet login），引导我完成 OTP 验证；3) 安装 plugin-store 和 hyperliquid-plugin（npx skills add + 二进制安装）；4) 执行 hyperliquid-plugin register 注册签名地址；5) 运行 hyperliquid-plugin quickstart 检查状态；6) 从已安装 skill 的 strategy-bundle/ 目录复制策略代码到 ~/hyperalpha-adaptive（不需要网络下载）；7) 跑 py_compile、validate-config、evaluate、scan 四项 dry-run 校验；8) 创建 ~/hyperalpha-adaptive/hyperalpha_official_path.sh 启动器。完成后告诉我安装路径和下一步命令。
```
