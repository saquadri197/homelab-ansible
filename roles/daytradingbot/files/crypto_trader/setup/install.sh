#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Crypto Trader — Server Installation Script
# Run this once on your ESXi VM (Ubuntu 22.04 recommended)
#
# Usage:
#   chmod +x setup/install.sh
#   sudo ./setup/install.sh
#
# What this does:
#   1. Installs Python 3.11, nginx, and certbot
#   2. Creates a dedicated 'trader' user
#   3. Sets up the project at /opt/crypto_trader
#   4. Creates a Python virtual environment and installs dependencies
#   5. Installs the systemd service for 24/7 operation
#   6. Configures nginx as a reverse proxy
# ═══════════════════════════════════════════════════════════════════════════

set -e   # Exit on any error

# ── Colour output ─────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Must run as root ──────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run this script with sudo."

INSTALL_DIR="/opt/crypto_trader"
SERVICE_USER="trader"

info "Starting Crypto Trader installation..."

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3.11 python3.11-venv python3.11-dev \
    python3-pip \
    nginx \
    certbot python3-certbot-nginx \
    git curl openssl \
    build-essential

# ── 2. Create dedicated user ──────────────────────────────────────────────────
if id "$SERVICE_USER" &>/dev/null; then
    info "User '$SERVICE_USER' already exists"
else
    info "Creating user '$SERVICE_USER'..."
    useradd --system --shell /bin/bash --home "$INSTALL_DIR" --create-home "$SERVICE_USER"
fi

# ── 3. Copy project files ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
info "Copying project from $SCRIPT_DIR to $INSTALL_DIR..."

rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='venv' --exclude='node_modules' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── 4. Python virtual environment ────────────────────────────────────────────
info "Creating Python virtual environment..."
sudo -u "$SERVICE_USER" python3.11 -m venv "$INSTALL_DIR/venv"

info "Installing Python dependencies..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --quiet \
    -r "$INSTALL_DIR/requirements.txt"

# ── 5. Self-signed SSL certificate (fallback if no domain) ───────────────────
if [[ ! -f /etc/ssl/certs/crypto-trader.crt ]]; then
    info "Generating self-signed SSL certificate..."
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/ssl/private/crypto-trader.key \
        -out    /etc/ssl/certs/crypto-trader.crt \
        -subj "/CN=$(hostname)/O=CryptoTrader/C=US" \
        2>/dev/null
    chmod 600 /etc/ssl/private/crypto-trader.key
    info "Self-signed cert created (valid 10 years)"
    warn "For production, get a real cert: sudo certbot --nginx -d yourdomain.com"
fi

# ── 6. nginx config ───────────────────────────────────────────────────────────
info "Configuring nginx..."
cp "$INSTALL_DIR/setup/nginx.conf" /etc/nginx/sites-available/crypto-trader
ln -sf /etc/nginx/sites-available/crypto-trader \
        /etc/nginx/sites-enabled/crypto-trader

# Remove default nginx site
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx || error "nginx config test failed"
systemctl enable nginx

# ── 7. systemd service ────────────────────────────────────────────────────────
info "Installing systemd service..."
sed "s|/opt/crypto_trader|$INSTALL_DIR|g" \
    "$INSTALL_DIR/setup/crypto-trader.service" \
    > /etc/systemd/system/crypto-trader.service

systemctl daemon-reload
systemctl enable crypto-trader

# ── 8. Firewall ───────────────────────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    info "Configuring firewall..."
    ufw allow 22/tcp   comment "SSH"
    ufw allow 80/tcp   comment "HTTP (Let's Encrypt)"
    ufw allow 443/tcp  comment "HTTPS (webhooks)"
    ufw --force enable
fi

# ── 9. Check config.yaml ──────────────────────────────────────────────────────
if grep -q "YOUR_API_KEY_HERE" "$INSTALL_DIR/config.yaml" 2>/dev/null; then
    warn "config.yaml still has placeholder values — edit before starting!"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. Edit your config:"
echo "     sudo nano $INSTALL_DIR/config.yaml"
echo "     → Add Kraken API key, Telegram token, webhook secret"
echo ""
echo "  2. Start the service:"
echo "     sudo systemctl start crypto-trader"
echo "     sudo systemctl status crypto-trader"
echo ""
echo "  3. Test the webhook endpoint:"
echo "     curl https://$(hostname -I | awk '{print $1}')/health -k"
echo ""
echo "  4. Port forward on your router:"
echo "     External 443 → $(hostname -I | awk '{print $1}'):443"
echo ""
echo "  5. Set your TradingView webhook URL to:"
echo "     https://YOUR_PUBLIC_IP/webhook"
echo ""
echo "  6. View live logs:"
echo "     sudo journalctl -u crypto-trader -f"
echo ""
