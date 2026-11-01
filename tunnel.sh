#!/usr/bin/env bash
# ==============================================================================
# WebBlock Platform: tunnel.sh
# Creates a secure ngrok Tunnel to expose the Combined Swagger Portal (Port 8088)
# to external teammates for remote testing without port forwarding or VPN.
# ==============================================================================
set -e

PORT="${1:-8088}"

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_CYAN="\033[36m"
C_YELLOW="\033[33m"

echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}                WebBlock Platform — ngrok Remote Tunnel               ${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"

if ! command -v ngrok &>/dev/null; then
    echo -e "${C_YELLOW}ngrok is not installed.${C_RESET} Installing via Homebrew..."
    brew install ngrok/ngrok/ngrok
fi

# Verify local portal is listening
if ! nc -z localhost "$PORT" 2>/dev/null; then
    echo -e "${C_YELLOW}⚠️  Port $PORT is not currently active.${C_RESET}"
    echo -e "   Please run ./start-all.sh first to start the microservices & portal."
    echo ""
    read -p "Start all services now? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ./start-all.sh
    else
        exit 1
    fi
fi

echo -e "\n${C_GREEN}✔ Starting ngrok Tunnel for http://localhost:$PORT...${C_RESET}"
echo -e "${C_CYAN}Sharing this public HTTPS link gives your teammates full access to:${C_RESET}"
echo -e "  • Combined Swagger UI Dashboard with live service selector"
echo -e "  • Interactive 'Try it out' API execution"
echo -e "  • Direct REST endpoints (/api/user/**, /api/v1/**, /v1/**, etc.)"
echo -e "----------------------------------------------------------------------"
echo -e "Press ${C_BOLD}Ctrl + C${C_RESET} at any time to close the tunnel.\n"

exec ngrok http "$PORT"
