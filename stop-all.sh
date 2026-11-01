#!/usr/bin/env bash
# ==============================================================================
# WebBlock Platform: stop-all.sh
# Gracefully stops all running microservices and cleans up process locks.
# ==============================================================================
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_DIR="$ROOT_DIR/.pids"

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_RED="\033[31m"
C_YELLOW="\033[33m"
C_CYAN="\033[36m"

echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}               WebBlock Platform — Stopping All Services              ${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"

STOP_DOCKER=false

for arg in "$@"; do
    case "$arg" in
        --all|--with-docker|-a)
            STOP_DOCKER=true
            ;;
        --help|-h)
            echo "Usage: ./stop-all.sh [options]"
            echo ""
            echo "Options:"
            echo "  --all, --with-docker   Also stop Docker Compose infrastructure containers"
            echo "  -h, --help             Show this help message"
            exit 0
            ;;
    esac
done

# ------------------------------------------------------------------------------
# 1. Stop Microservices via PID files
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}[1/2] Terminating Microservice Background Processes...${C_RESET}"

if [ -d "$PID_DIR" ]; then
    for pidfile in "$PID_DIR"/*.pid; do
        if [ -f "$pidfile" ]; then
            service_name="$(basename "$pidfile" .pid)"
            pid="$(cat "$pidfile" 2>/dev/null || true)"
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                echo -n "   Stopping $service_name (PID: $pid)..."
                kill -TERM "$pid" 2>/dev/null || true
                
                # Wait up to 5 seconds for graceful shutdown
                for i in {1..10}; do
                    if ! kill -0 "$pid" 2>/dev/null; then
                        break
                    fi
                    sleep 0.5
                done
                
                # Force kill if still alive
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                    echo -e " ${C_YELLOW}forced (SIGKILL).${C_RESET}"
                else
                    echo -e " ${C_GREEN}stopped.${C_RESET}"
                fi
            else
                echo -e "   $service_name is not running."
            fi
            rm -f "$pidfile"
        fi
    done
fi

# Clean up any remaining processes bound to service ports
SERVICE_PORTS=(8000 8080 8081 8084 8085 8088 8089 9090)
for port in "${SERVICE_PORTS[@]}"; do
    pids="$(lsof -ti :"$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        for p in $pids; do
            # Skip docker container port proxies
            comm="$(ps -p "$p" -o comm= 2>/dev/null || true)"
            if [[ "$comm" != *"docker"* && "$comm" != *"com.docker"* ]]; then
                echo -e "   ${C_YELLOW}Cleaning up lingering process on port $port (PID: $p)...${C_RESET}"
                kill -9 "$p" 2>/dev/null || true
            fi
        done
    fi
done

echo -e "${C_GREEN}✔ All microservice processes stopped.${C_RESET}"

# ------------------------------------------------------------------------------
# 2. Stop Docker Infrastructure (if requested)
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}[2/2] Checking Docker Infrastructure...${C_RESET}"

if [ "$STOP_DOCKER" = true ]; then
    if docker info >/dev/null 2>&1; then
        echo -e "   Stopping Docker containers via docker-compose.integration.yml..."
        docker compose -f docker-compose.integration.yml down
        echo -e "${C_GREEN}✔ Docker infrastructure stopped.${C_RESET}"
    fi
else
    if docker info >/dev/null 2>&1; then
        running_containers="$(docker ps --filter "name=webblock-" -q 2>/dev/null || true)"
        if [ -n "$running_containers" ]; then
            echo -e "${C_CYAN}ℹ Docker infrastructure containers (Postgres, RabbitMQ, Keycloak, etc.) are still running.${C_RESET}"
            echo -e "  To also stop Docker containers, run: ${C_BOLD}./stop-all.sh --all${C_RESET}"
        else
            echo -e "   No WebBlock Docker containers currently running."
        fi
    fi
fi

echo -e "\n${C_BOLD}${C_GREEN}Shutdown complete.${C_RESET}\n"
