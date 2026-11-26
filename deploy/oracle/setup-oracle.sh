#!/usr/bin/env bash
set -euo pipefail

# One-shot Oracle Cloud (Oracle Linux 9 or Ubuntu) setup for ClipForge backend + free Caddy HTTPS
PUBLIC_IP="${1:-$(curl -s https://api.ipify.org)}"
DOMAIN="${2:-${PUBLIC_IP//./-}.sslip.io}"

echo "=== Setting up ClipForge on $DOMAIN (IP: $PUBLIC_IP) ==="

# 1. Install Docker + Compose plugin if missing
if ! command -v docker >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo systemctl enable --now docker
  else
    curl -fsSL https://get.docker.com | sudo sh
  fi
  sudo usermod -aG docker "$USER" || true
fi

# 2. Open ports 80 and 443 in OS firewall (firewalld on Oracle Linux, iptables on Ubuntu)
if command -v firewall-cmd >/dev/null 2>&1 && sudo systemctl is-active --quiet firewalld; then
  sudo firewall-cmd --permanent --add-service=http
  sudo firewall-cmd --permanent --add-service=https
  sudo firewall-cmd --reload
elif command -v iptables >/dev/null 2>&1; then
  sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save || true
  fi
fi

# 3. Start backend + Caddy with automatic Let's Encrypt HTTPS
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p ../../data
export CLIPFORGE_DOMAIN="$DOMAIN"

sudo -E docker compose up -d --build

echo ""
echo "=== ClipForge Backend Deployed! ==="
echo "HTTPS API URL: https://$DOMAIN"
echo "Health check:  https://$DOMAIN/api/health"
