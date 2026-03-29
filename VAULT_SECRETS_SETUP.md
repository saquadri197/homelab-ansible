# Vault Secrets Setup — Black Cat Trading Bot

Run these commands on **ansible01** (`192.168.3.129`) from `/home/saquadri/ansible/`.

---

## Step 1 — Open the vault file for editing

```bash
ansible-vault edit group_vars/vault.yml
```

This opens the file in your editor. Add the following block at the bottom,
replacing each placeholder with your real values:

```yaml
# ── Black Cat Trading Bot secrets ────────────────────────────
vault_tradingbot_blofin_api_key:    "your_blofin_api_key_here"
vault_tradingbot_blofin_api_secret: "your_blofin_api_secret_here"
vault_tradingbot_blofin_passphrase: "your_blofin_passphrase_here"
vault_tradingbot_telegram_bot_token: "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
vault_tradingbot_telegram_chat_id:   "987654321"
vault_tradingbot_webhook_secret:    "generate_with_command_below"
```

Save and close the editor. Ansible Vault will re-encrypt the file automatically.

---

## Step 2 — Generate a webhook secret

Run this on ansible01 to generate a strong random secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and use it as `vault_tradingbot_webhook_secret`.
You'll also need this value when setting up TradingView webhook headers.

---

## Step 3 — Where to find your Blofin credentials

1. Log in to Blofin → Account → API Management
2. Create a new API key with these settings:
   - **Permission**: Futures Trading ONLY (no withdrawals, no spot)
   - **IP Whitelist**: Add enigma's IP — `192.168.3.130`
3. Copy the API Key, Secret, and Passphrase into the vault

---

## Step 4 — Where to find your Telegram credentials

**Bot Token:**
1. Open Telegram, search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the token it gives you

**Chat ID:**
1. Send any message to your new bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Look for `"chat": {"id": 123456789}` — that number is your Chat ID

---

## Step 5 — Verify vault contents (optional)

```bash
ansible-vault view group_vars/vault.yml
```

---

## Step 6 — Run the deployment

Once all secrets are in the vault:

```bash
# Dry run first
ansible-playbook playbooks/deploy_tradingbot.yml --check --diff

# Full deploy
ansible-playbook playbooks/deploy_tradingbot.yml
```
