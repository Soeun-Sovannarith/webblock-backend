#!/usr/bin/env bash
# ==============================================================================
# WebBlock Platform: start-all.sh
# Starts shared infrastructure (Docker Compose) and all backend microservices.
# ==============================================================================
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_DIR="$ROOT_DIR/.pids"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_RED="\033[31m"
C_YELLOW="\033[33m"
C_CYAN="\033[36m"
C_BLUE="\033[34m"

echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}               WebBlock Platform — Starting All Services              ${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"

INFRA_ONLY=false
SERVICES_ONLY=false
ENABLE_TUNNEL=false

for arg in "$@"; do
    case "$arg" in
        --infra-only)
            INFRA_ONLY=true
            ;;
        --services-only)
            SERVICES_ONLY=true
            ;;
        --tunnel|-t)
            ENABLE_TUNNEL=true
            ;;
        --help|-h)
            echo "Usage: ./start-all.sh [options]"
            echo ""
            echo "Options:"
            echo "  --infra-only      Only start Docker Compose infrastructure (Postgres, RabbitMQ, Redis, Keycloak, Mailpit)"
            echo "  --services-only   Only start microservices (skip Docker Compose)"
            echo "  --tunnel, -t      Launch an ngrok Tunnel immediately after startup to share with teammates"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
    esac
done

# ------------------------------------------------------------------------------
# 1. Start Infrastructure (Docker Compose)
# ------------------------------------------------------------------------------
if [ "$SERVICES_ONLY" = false ]; then
    echo -e "\n${C_BOLD}[1/2] Checking Docker Infrastructure...${C_RESET}"
    if ! docker info >/dev/null 2>&1; then
        echo -e "${C_YELLOW}⚠️  Docker daemon is not running.${C_RESET}"
        echo -e "   Please start Docker Desktop if you need local PostgreSQL, RabbitMQ, and Keycloak."
        if [ "$INFRA_ONLY" = true ]; then
            echo -e "${C_RED}Error: --infra-only requested but Docker is not running.${C_RESET}"
            exit 1
        fi
        echo -e "   Proceeding to start services with existing/external endpoints...\n"
    else
        echo -e "${C_GREEN}✔ Docker is active.${C_RESET} Starting infrastructure via docker-compose.integration.yml..."
        docker compose -f docker-compose.integration.yml up -d
        echo -e "${C_GREEN}✔ Infrastructure services started (Postgres, RabbitMQ, Redis, Mailpit, Keycloak).${C_RESET}"
        
        # Wait for PostgreSQL & RabbitMQ port readiness
        echo -n "   Waiting for database port 5432 and broker port 5672..."
        for i in {1..30}; do
            if nc -z localhost 5432 2>/dev/null && nc -z localhost 5672 2>/dev/null; then
                echo -e " ${C_GREEN}Ready!${C_RESET}"
                break
            fi
            echo -n "."
            sleep 1
        done
    fi
fi

if [ "$INFRA_ONLY" = true ]; then
    echo -e "\n${C_GREEN}${C_BOLD}Infrastructure startup complete!${C_RESET}"
    exit 0
fi

# ------------------------------------------------------------------------------
# Common Environment Defaults
# ------------------------------------------------------------------------------
export DB_URL="jdbc:postgresql://localhost:5432/webblock_user"
export DB_USER="account"
export DB_PASSWORD="account"
export FLYWAY_USER="account"
export FLYWAY_PASSWORD="account"

export PROJECT_DB_URL="jdbc:postgresql://localhost:5432/webblock_project"
export PROJECT_DB_APP_USER="project_app_login"
export PROJECT_DB_APP_PASSWORD="project_secret"
export PROJECT_DB_MIGRATOR_USER="project_migrator_login"
export PROJECT_DB_MIGRATOR_PASSWORD="project_secret"

export CATALOG_DB_URL="jdbc:postgresql://localhost:5432/catalog"
export CATALOG_DB_PLATFORM_USER="catalog_platform_login"
export CATALOG_DB_PLATFORM_PASSWORD="catalog_secret"
export CATALOG_DB_MIGRATOR_USER="catalog_migrator_login"
export CATALOG_DB_MIGRATOR_PASSWORD="catalog_secret"

export RABBITMQ_HOST="localhost"
export RABBITMQ_PORT="5672"
export RABBITMQ_USER="webblock"
export RABBITMQ_PASSWORD="webblock"
export RABBITMQ_USERNAME="webblock"

export REDIS_HOST="localhost"
export REDIS_PORT="6379"

# Mail & Keycloak: safely load properties from services/webblock-user-service/.env if present
if [ -f "$ROOT_DIR/services/webblock-user-service/.env" ]; then
    while IFS='=' read -r key val || [ -n "$key" ]; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [ -z "$key" ] && continue
        if [[ "$key" =~ ^(MAIL_|EMAIL|GOOGLE_APP_PASSWORD|KEYCLOAK_) ]]; then
            val="${val%\"}"
            val="${val#\"}"
            export "$key=$val"
        fi
    done < "$ROOT_DIR/services/webblock-user-service/.env"
fi
export MAIL_HOST="${MAIL_HOST:-localhost}"
export MAIL_PORT="${MAIL_PORT:-1025}"
export MAIL_SMTP_AUTH="${MAIL_SMTP_AUTH:-false}"
export MAIL_STARTTLS="${MAIL_STARTTLS:-false}"

# Notification: safely load knock / alertmanager properties from services/webblock-notification-service/.env
if [ -f "$ROOT_DIR/services/webblock-notification-service/.env" ]; then
    while IFS='=' read -r key val || [ -n "$key" ]; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [ -z "$key" ] && continue
        if [[ "$key" =~ ^(KNOCK_|ALERTMANAGER_) ]]; then
            val="${val%\"}"
            val="${val#\"}"
            export "$key=$val"
        fi
    done < "$ROOT_DIR/services/webblock-notification-service/.env"
fi

# Load root .env if present (overrides service-level defaults)
if [ -f "$ROOT_DIR/.env" ]; then
    while IFS='=' read -r key val || [ -n "$key" ]; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [ -z "$key" ] && continue
        val="${val%\"}"
        val="${val#\"}"
        export "$key=$val"
    done < "$ROOT_DIR/.env"
fi

export KEYCLOAK_SERVER_URL="${KEYCLOAK_SERVER_URL:-http://localhost:8180}"
export KEYCLOAK_ISSUER_URI="${KEYCLOAK_ISSUER_URI:-http://localhost:8180/realms/webblock}"
export KEYCLOAK_ADMIN_URL="${KEYCLOAK_ADMIN_URL:-http://localhost:8180}"
export KEYCLOAK_ADMIN_CLIENT_ID="${KEYCLOAK_ADMIN_CLIENT_ID:-webblock-auth-admin}"
export KEYCLOAK_ADMIN_CLIENT_SECRET="${KEYCLOAK_ADMIN_CLIENT_SECRET:-}"
export KEYCLOAK_ADMIN_CF_CLIENT_ID="${KEYCLOAK_ADMIN_CF_CLIENT_ID:-}"
export KEYCLOAK_ADMIN_CF_CLIENT_SECRET="${KEYCLOAK_ADMIN_CF_CLIENT_SECRET:-}"
export KEYCLOAK_PASSWORD_CHECK_CLIENT_ID="${KEYCLOAK_PASSWORD_CHECK_CLIENT_ID:-webblock-password-check}"
export KEYCLOAK_PASSWORD_CHECK_CLIENT_SECRET="${KEYCLOAK_PASSWORD_CHECK_CLIENT_SECRET:-}"
export KEYCLOAK_AUDIENCE="${KEYCLOAK_AUDIENCE:-webblock-account}"
export EUREKA_CLIENT_ENABLED="${EUREKA_CLIENT_ENABLED:-false}"

export PROJECT_SERVICE_URL="http://localhost:8080"
export PROJECT_MANAGEMENT_PORT="8089"
export SERVER_PORT_USER="8081"
export SERVER_PORT_NOTIFICATION="8085"

# Security & token validation for project-service
export PROJECT_JWT_ISSUER_URI="http://localhost:8180/realms/webblock"
export PROJECT_IDENTITY_MODE="jwt"
export SPRING_PROFILES_ACTIVE="prototype"
export PROJECT_DEV_TOKENS_FILE="$ROOT_DIR/services/webblock-project-service/dev-tokens.json"
export PROJECT_PAYMENT_STAND_IN_FILE="$ROOT_DIR/services/webblock-project-service/payment-stand-in.json"

# ------------------------------------------------------------------------------
# 2. Start Microservices
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}[2/2] Starting Microservices in Background...${C_RESET}"

start_service() {
    local name="$1"
    local dir="$2"
    local cmd="$3"
    local pidfile="$PID_DIR/$name.pid"
    local logfile="$LOG_DIR/$name.log"

    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo -e "   ${C_YELLOW}● $name is already running (PID: $(cat "$pidfile"))${C_RESET}"
        return
    fi

    echo -e "   ${C_CYAN}▶ Starting $name...${C_RESET}"
    (
        cd "$dir"
        eval "$cmd" > "$logfile" 2>&1
    ) &
    local pid=$!
    echo "$pid" > "$pidfile"
    echo -e "     PID: $pid | Logs: ${logfile#"${ROOT_DIR}/"}"
}

# 1. User Service (Spring Boot 4, Port 8081)
if [ -d "services/webblock-user-service" ]; then
    chmod +x services/webblock-user-service/gradlew 2>/dev/null || true
    start_service "user-service" "services/webblock-user-service" "./gradlew bootRun"
fi

# 2. Project Service (Spring Boot 4, Port 8080 / Mgmt 8089 / gRPC 9090)
if [ -d "services/webblock-project-service" ]; then
    chmod +x services/webblock-project-service/gradlew 2>/dev/null || true
    chmod 600 "$PROJECT_DEV_TOKENS_FILE" 2>/dev/null || true
    chmod 600 "$PROJECT_PAYMENT_STAND_IN_FILE" 2>/dev/null || true
    start_service "project-service" "services/webblock-project-service" "./gradlew bootRun"
fi

# 3. AI Code Service (FastAPI, Port 8000)
if [ -d "services/webblock-aicode-service" ]; then
    PYTHON_BIN="$ROOT_DIR/services/webblock-aicode-service/.venv/bin/uvicorn"
    if [ ! -f "$PYTHON_BIN" ]; then
        PYTHON_BIN="uvicorn"
    fi
    start_service "aicode-service" "services/webblock-aicode-service" "$PYTHON_BIN app.main:app --host 0.0.0.0 --port 8000"
fi

# 4. Provisioning Service (FastAPI, Port 8084 / Ops 18084)
if [ -d "services/webblock-provisioning-service" ]; then
    PY_BIN="$ROOT_DIR/services/webblock-provisioning-service/.venv/bin/python"
    if [ ! -f "$PY_BIN" ]; then
        PY_BIN="python3"
    fi
    start_service "provisioning-service" "services/webblock-provisioning-service" "unset PROJECT_SERVICE_URL && set -a && source ./.env.api && set +a && PYTHONPATH=src $PY_BIN -m provisioning.hosts.api"
fi

# 5. Notification Service (Spring Boot 4, Port 8085)
if [ -d "services/webblock-notification-service" ]; then
    chmod +x services/webblock-notification-service/gradlew 2>/dev/null || true
    start_service "notification-service" "services/webblock-notification-service" "SPRING_PROFILES_ACTIVE=local-stub SERVER_PORT=8085 ./gradlew bootRun"
fi

# 6. Combined Swagger / OpenAPI Portal (Port 8088)
if [ -f "tooling/swagger/server.py" ]; then
    start_service "swagger-portal" "tooling/swagger" "python3 $ROOT_DIR/tooling/swagger/server.py"
fi

# ------------------------------------------------------------------------------
# 3. Summary & Endpoints Table
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}${C_GREEN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}              Services Launched! Endpoint & Port Reference            ${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}======================================================================${C_RESET}"
printf "%-26s %-8s %-36s\n" "SERVICE" "PORT" "URL / HEALTH / DOCS"
echo "----------------------------------------------------------------------"
printf "%-26s %-8s %-36s\n" "Combined Swagger Portal" "8088" "http://localhost:8088"
echo "----------------------------------------------------------------------"
printf "%-26s %-8s %-36s\n" "User Service" "8081" "http://localhost:8081/swagger-ui.html"
printf "%-26s %-8s %-36s\n" "Project Service" "8080" "http://localhost:8089/actuator/health"
printf "%-26s %-8s %-36s\n" "AI Code Service" "8000" "http://localhost:8000/docs"
printf "%-26s %-8s %-36s\n" "Provisioning Service" "8084" "http://localhost:18084/healthz"
printf "%-26s %-8s %-36s\n" "Notification Service" "8085" "http://localhost:8085/actuator/health"
echo "----------------------------------------------------------------------"
printf "%-26s %-8s %-36s\n" "Keycloak Auth" "8180" "http://localhost:8180 (admin/adminpassword)"
printf "%-26s %-8s %-36s\n" "RabbitMQ Management" "15672" "http://localhost:15672 (webblock/webblock)"
printf "%-26s %-8s %-36s\n" "Mailpit Web Mail" "8025" "http://localhost:8025"
echo -e "----------------------------------------------------------------------\n"
echo -e "${C_CYAN}🌟 Combined Swagger UI:${C_RESET} http://localhost:8088"
echo -e "${C_CYAN}🌐 Share with team:${C_RESET}     ./tunnel.sh (ngrok Tunnel)"
echo -e "${C_CYAN}💡 View live logs:${C_RESET}       tail -f logs/<service-name>.log"
echo -e "${C_CYAN}💡 Stop all services:${C_RESET}   ./stop-all.sh"
echo -e "${C_CYAN}💡 Verify integration:${C_RESET}  services/webblock-aicode-service/.venv/bin/python tooling/integration/verify_integration.py\n"

if [ "$ENABLE_TUNNEL" = true ]; then
    ./tunnel.sh
fi
