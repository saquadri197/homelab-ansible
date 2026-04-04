# Deployment Runbook — bandit.syedaq.local (Debian 12)

Run all commands from your **Ansible server**, inside the `ansible/` directory.

---

## One-time setup (on your Ansible server)

```bash
# 1. Install Ansible if not already installed
pip install ansible

# 2. Install required collections
ansible-galaxy collection install -r requirements.yml

# 3. Confirm SSH access to bandit
ansible bandit -m ping
# Expected: bandit | SUCCESS => {"ping": "pong"}
```

---

## Create vault secrets (one-time)

```bash
# Generate a webhook secret
python3 -c "import secrets; print(secrets.token_hex(32))"
# Copy the output — you'll paste it into the vault AND TradingView alert JSON

# Create and encrypt the secrets file
ansible-vault create vars/secrets.yml
```

Paste this into the vault editor:

```yaml
kraken_api_key:     "your_kraken_api_key"
kraken_api_secret:  "your_kraken_api_secret"
telegram_bot_token: "your_telegram_bot_token"
telegram_chat_id:   "your_chat_id"
webhook_secret:     "the_hex_string_you_generated_above"
```

---

## Update inventory.yml

Edit `ansible/inventory.yml` and set `ansible_user` to your SSH user on bandit.

---

## Full deploy (first time)

```bash
ansible-playbook playbook.yml --ask-vault-pass --ask-become-pass
```

---

## Code update only (after changing Python files)

```bash
ansible-playbook playbook.yml --tags deploy --ask-vault-pass --ask-become-pass
```

---

## Restart service only

```bash
ansible-playbook playbook.yml --tags restart --ask-become-pass
```

---

## Verify it's running (from Ansible server or any machine on your LAN)

```bash
curl http://192.168.3.131:5000/health
# {"status":"ok","time":"...","open_positions":[]}
```

---

## Add cloudflared tunnel route (on bandit directly, or via Ansible tunnel tag)

```bash
# Option A — Ansible handles it (if cloudflared config path is correct in vars/main.yml)
ansible-playbook playbook.yml --tags tunnel --ask-become-pass

# Option B — manual, on bandit
sudo nano /etc/cloudflared/config.yml
# Add snippet from cloudflared/tunnel-config.yml
sudo systemctl restart cloudflared
```

Then verify the public URL works:

```bash
curl https://bandit.syedaq.com/health
```

---

## Monitor logs on bandit

```bash
ssh bandit.syedaq.local
sudo journalctl -u crypto-trader -f
```

---

## Flip to live trading (when ready)

Edit `ansible/roles/crypto-trader/vars/main.yml`:
```yaml
dry_run: false
```

Then re-deploy config:
```bash
ansible-playbook playbook.yml --tags deploy --ask-vault-pass --ask-become-pass
```
