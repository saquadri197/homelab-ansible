# syedaq.com Homelab — Ansible Automation Platform
**Control node:** ansible01.syedaq.com | **User:** saquadri | **Venv:** `~/ansible/.venv`

---

## First-time setup on ansible01

```bash
# 1. Clone / copy this repo onto ansible01
scp -r ansible-homelab/ saquadri@ansible01.syedaq.com:~/ansible/

# 2. Run the bootstrap script (once only)
ssh saquadri@ansible01.syedaq.com
cd ~/ansible
chmod +x bootstrap.sh
./bootstrap.sh
# Prompts you to set a vault password and enter your Portainer admin password.
# Creates ~/.ansible_vault_pass (chmod 600) and encrypts group_vars/vault.yml.

# 3. Activate the venv for all future sessions
source ~/ansible/.venv/bin/activate
cd ~/ansible
```

---

## Daily workflow

```bash
source ~/ansible/.venv/bin/activate
cd ~/ansible
```

---

## Hardening

```bash
# Dry run first — always
ansible-playbook playbooks/harden_only.yml --check --diff

# Apply to entire fleet (batches of 5)
ansible-playbook playbooks/harden_only.yml

# Single host
ansible-playbook playbooks/harden_only.yml --limit wekan01

# UFW + root SSH only (skip fail2ban)
ansible-playbook playbooks/harden_only.yml --tags "common,ufw"
```

---

## Docker discovery

```bash
# Scan all managed_linux hosts for a running Docker daemon
ansible-playbook playbooks/discover_docker.yml

# Review the output
cat inventory/docker_hosts_discovered.ini

# Promote confirmed hosts into [docker_hosts] in inventory/hosts.ini
```

---

## Portainer agent deployment

```bash
# Deploy to all [docker_hosts]
ansible-playbook playbooks/deploy_portainer_agents.yml

# Single new host
ansible-playbook playbooks/deploy_portainer_agents.yml --limit enigma
```

---

## Portainer endpoint registration (API)

```bash
# Register all [docker_hosts] endpoints in Portainer (skips already-registered)
ansible-playbook playbooks/register_portainer_endpoints.yml
# vault password is read automatically from ~/.ansible_vault_pass
```

---

## New host onboarding (full pipeline)

```bash
# 1. Add host to inventory/hosts.ini under [managed_linux]
#    If it runs Docker, also add to [docker_hosts]

# 2. Harden
ansible-playbook playbooks/harden_only.yml --limit <hostname>

# 3. Deploy Docker + agent (if applicable)
ansible-playbook playbooks/deploy_portainer_agents.yml --limit <hostname>

# 4. Register in Portainer
ansible-playbook playbooks/register_portainer_endpoints.yml
```

---

## Vault operations

```bash
# View the decrypted vault
ansible-vault view group_vars/vault.yml

# Edit vault contents (e.g. rotate Portainer password)
ansible-vault edit group_vars/vault.yml

# Re-encrypt with a new vault password
ansible-vault rekey group_vars/vault.yml
```

---

## Tags reference

| Tag | Scope |
|-----|-------|
| `common` | Packages, sudo, MOTD, auto-upgrades |
| `baseline` | Alias for common |
| `hardening` | common + security |
| `ufw` | UFW rules only |
| `ssh` | SSH sshd_config changes |
| `fail2ban` | fail2ban install + jail config |
| `docker` | Docker CE install |
| `portainer_agent` | Agent container deployment |
| `portainer` | Alias for portainer_agent |

---

## Per-host overrides

Create `host_vars/<hostname>.yml` to override any variable. Example — allow an extra port on a specific host:

```yaml
# host_vars/wordpress.yml
ufw_allowed_tcp_ports:
  - 22
  - 80
  - 443
```

Override Portainer agent port on a specific host:
```yaml
# host_vars/enigma.yml
portainer_agent_port: 9002
```
