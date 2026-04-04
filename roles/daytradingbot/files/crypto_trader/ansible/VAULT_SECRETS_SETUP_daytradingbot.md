# Vault Secrets Setup — Kraken Day Trading Bot

Run these commands on **ansible01** (`192.168.3.129`) from `/home/saquadri/ansible/`.

---

## Step 1 — Add vault secrets

```bash
ansible-vault edit group_vars/vault.yml
```

Add the following block at the bottom (or anywhere in the file), replacing each placeholder:

```yaml
# ── Kraken Day Trading Bot secrets ───────────────────────────
vault_daytradingbot_kraken_api_key:    "your_kraken_futures_api_key"
vault_daytradingbot_kraken_api_secret: "your_kraken_futures_api_secret"
vault_daytradingbot_telegram_bot_token: "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
vault_daytradingbot_telegram_chat_id:   "987654321"
vault_daytradingbot_webhook_secret:    "generate_with_command_below"
```

Save and close — Ansible Vault re-encrypts automatically.

---

## Step 2 — Generate a webhook secret

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and paste it as `vault_daytradingbot_webhook_secret` above.
Also paste it into your TradingView alert JSON as the `"secret"` field.

---

## Step 3 — Get your Kraken API key

1. Log in to [futures.kraken.com](https://futures.kraken.com)
2. Go to **Settings → API** and create a new key
3. Enable: **Trading** permission (read + trade)
4. Copy the API key and secret into the vault above

For paper trading (testnet), use [demo-futures.kraken.com](https://demo-futures.kraken.com) — keys from the demo site do NOT work on live, and vice versa.

---

## Step 4 — Add non-secret overrides to host_vars/bandit.yml

These go in `host_vars/bandit.yml` (not vault — not sensitive):

```yaml
# ── Kraken Day Trading Bot (non-secret settings) ──────────────
# Secrets (API keys, tokens) live in group_vars/vault.yml
daytradingbot_dry_run:              "true"     # Change to "false" ONLY after paper trading
daytradingbot_kraken_testnet:       "true"     # Change to "false" for live trading
daytradingbot_min_confluence_score: "60"
daytradingbot_risk_per_trade_pct:   "1.0"
daytradingbot_max_open_positions:   "3"
```

---

## Step 5 — Copy bot source into the Ansible role

The Ansible role expects the `crypto_trader/` Python package to be in:

```
roles/daytradingbot/files/crypto_trader/
```

Copy the full source directory from your dev machine:

```bash
# From your Windows machine / Samba share — run on ansible01:
cp -r /path/to/crypto_trader roles/daytradingbot/files/crypto_trader

# Or use rsync from wherever you have the source:
rsync -av crypto_trader/ ansible01.syedaq.local:/home/saquadri/ansible/roles/daytradingbot/files/crypto_trader/
```

The role will deploy this directory to `/opt/daytradingbot/` on bandit.

---

## Step 6 — Deploy

```bash
# Dry run first
ansible-playbook playbooks/deploy_daytradingbot.yml --check --diff

# Full deploy
ansible-playbook playbooks/deploy_daytradingbot.yml
```

---

## Step 7 — Verify

```bash
# LAN health check (direct)
curl http://192.168.3.131:5000/health

# Public health check (through Cloudflare Tunnel)
curl https://bandit.syedaq.com/health

# Watch logs
ssh bandit.syedaq.local
sudo journalctl -u daytradingbot -f
```

---

## Flip to live trading (when ready)

Edit `host_vars/bandit.yml`:
```yaml
daytradingbot_dry_run:        "false"
daytradingbot_kraken_testnet: "false"
```

And update vault with **live** Kraken API credentials (testnet keys won't work on live).

Then re-deploy:
```bash
ansible-playbook playbooks/deploy_daytradingbot.yml
```
