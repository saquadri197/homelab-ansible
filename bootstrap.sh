#!/usr/bin/env bash
# =============================================================
# bootstrap.sh
# Run once on ansible01 to set up the Ansible project structure,
# install dependencies, and initialise Ansible Vault.
#
# Usage:
#   chmod +x bootstrap.sh
#   ./bootstrap.sh
# =============================================================

set -euo pipefail

ANSIBLE_DIR="/home/saquadri/ansible"
VENV_DIR="$ANSIBLE_DIR/.venv"

echo "==> Creating directory structure under $ANSIBLE_DIR"
mkdir -p "$ANSIBLE_DIR"/{roles/{common/{tasks,handlers},security/{tasks,handlers},docker/{tasks,handlers},portainer_agent/{tasks,handlers}},playbooks,inventory,group_vars,host_vars}

echo "==> Copying project files (run from the directory containing this script)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -av --exclude=bootstrap.sh --exclude='.git' "$SCRIPT_DIR/" "$ANSIBLE_DIR/"

echo "==> Setting up Python venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip ansible

echo "==> Verifying Ansible version"
ansible --version

echo "==> Setting up Ansible Vault password file"
VAULT_PASS_FILE="$HOME/.ansible_vault_pass"
if [ ! -f "$VAULT_PASS_FILE" ]; then
    echo "Enter a strong vault password (stored in $VAULT_PASS_FILE, chmod 600):"
    read -rs VAULT_PASS
    echo "$VAULT_PASS" > "$VAULT_PASS_FILE"
    chmod 600 "$VAULT_PASS_FILE"
    echo "Vault password file created."
else
    echo "Vault password file already exists at $VAULT_PASS_FILE — skipping."
fi

echo ""
echo "==> Creating encrypted vault file at $ANSIBLE_DIR/group_vars/vault.yml"
echo "    You will be prompted to enter your Portainer admin password."
echo ""

PORTAINER_PASS=""
while [ -z "$PORTAINER_PASS" ]; do
    read -rsp "Portainer admin password: " PORTAINER_PASS
    echo ""
done

ansible-vault encrypt_string \
    --vault-password-file "$VAULT_PASS_FILE" \
    --encrypt-vault-id default \
    "$PORTAINER_PASS" \
    --name vault_portainer_admin_password \
    > "$ANSIBLE_DIR/group_vars/vault.yml"

echo ""
echo "==> Testing SSH connectivity to managed_linux group"
cd "$ANSIBLE_DIR"
ansible managed_linux -i inventory/hosts.ini -m ping --vault-password-file "$VAULT_PASS_FILE" || true

echo ""
echo "======================================================"
echo " Bootstrap complete!"
echo " Ansible dir : $ANSIBLE_DIR"
echo " Virtual env : $VENV_DIR"
echo " Vault pass  : $VAULT_PASS_FILE"
echo ""
echo " Next steps:"
echo "   source $VENV_DIR/bin/activate"
echo "   cd $ANSIBLE_DIR"
echo "   ansible-playbook playbooks/harden_only.yml --vault-password-file ~/.ansible_vault_pass --check --diff"
echo "======================================================"
