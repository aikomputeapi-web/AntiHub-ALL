#!/bin/bash
# AntiHub-ALL One-Click Deployment Script
# For Linux systems

set -e

# Ensure the script runs from its own directory (avoid missing compose/.env when run elsewhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_prompt() {
    echo -e "${BLUE}[INPUT]${NC} $1"
}

# Check if a command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed. Please install $1 first"
        exit 1
    fi
}

# Generate a random secret key
generate_random_key() {
    if command -v openssl &> /dev/null; then
        openssl rand -hex 32
        return
    fi

    # Docker-only environment: use a container to generate random values, avoiding host openssl dependency
    docker run --rm python:3.11-alpine python -c "import secrets; print(secrets.token_hex(32))"
}

# Generate a Fernet key (used for PLUGIN_API_ENCRYPTION_KEY)
generate_fernet_key() {
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || \
    docker run --rm python:3.11-alpine python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
}

# Read user input (with default value)
read_with_default() {
    local prompt="$1"
    local default="$2"
    local value

    read -p "$prompt [$default]: " value
    # Handle terminals/pastes that may include CR (\r): prevent errors when writing to .env / parsing variables
    value=${value//$'\r'/}
    echo "${value:-$default}"
}

# Read password (plaintext input, single entry)
read_password() {
    local prompt="$1"
    local min_length="${2:-1}"
    local password

    while true; do
        # As per user requirements: plaintext input once, no secondary confirmation
        read -p "$prompt: " password
        password=${password//$'\r'/}
        if [ -z "$password" ]; then
            log_error "Password cannot be empty"
            continue
        fi
        if [ "${#password}" -lt "$min_length" ]; then
            log_error "Password must be at least ${min_length} characters"
            continue
        fi
        echo "$password"
        break
    done
}

# Write .env file: does not rely on sed (avoids parsing errors from special characters / terminal pastes)
write_env_file() {
    local env_file="$1"
    local tmp_file="${env_file}.tmp"

    local postgres_user
    local postgres_db
    postgres_user=$(grep "^POSTGRES_USER=" "$env_file" | cut -d'=' -f2- 2>/dev/null || true)
    postgres_db=$(grep "^POSTGRES_DB=" "$env_file" | cut -d'=' -f2- 2>/dev/null || true)
    postgres_user=${postgres_user//$'\r'/}
    postgres_db=${postgres_db//$'\r'/}
    postgres_user=${postgres_user:-antihub}
    postgres_db=${postgres_db:-antihub}

    while IFS= read -r line || [ -n "$line" ]; do
        line=${line//$'\r'/}
        case "$line" in
            WEB_PORT=*)
                printf '%s\n' "WEB_PORT=$WEB_PORT"
                ;;
            BACKEND_PORT=*)
                printf '%s\n' "BACKEND_PORT=$BACKEND_PORT"
                ;;
            \#\ POSTGRES_PORT=*|\#POSTGRES_PORT=*|POSTGRES_PORT=*)
                printf '%s\n' "POSTGRES_PORT=$POSTGRES_PORT"
                ;;
            COOKIE_HTTP=*)
                printf '%s\n' "COOKIE_HTTP=$COOKIE_HTTP"
                ;;
            ADMIN_USERNAME=*)
                printf '%s\n' "ADMIN_USERNAME=$ADMIN_USERNAME"
                ;;
            ADMIN_PASSWORD=*)
                printf '%s\n' "ADMIN_PASSWORD=$ADMIN_PASSWORD"
                ;;
            JWT_SECRET_KEY=*)
                printf '%s\n' "JWT_SECRET_KEY=$JWT_SECRET"
                ;;
            POSTGRES_PASSWORD=*)
                printf '%s\n' "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
                ;;
            PLUGIN_API_ENCRYPTION_KEY=*)
                printf '%s\n' "PLUGIN_API_ENCRYPTION_KEY=$ENCRYPTION_KEY"
                ;;
            DATABASE_URL=*)
                printf '%s\n' "DATABASE_URL=postgresql+asyncpg://${postgres_user}:${POSTGRES_PASSWORD}@postgres:5432/${postgres_db}"
                ;;
            *)
                printf '%s\n' "$line"
                ;;
        esac
    done < "$env_file" > "$tmp_file"

    mv "$tmp_file" "$env_file"
}

get_env_value() {
    local file="$1"
    local key="$2"
    if [ ! -f "$file" ]; then
        return 0
    fi
    local value
    value=$(grep -m 1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2- || true)
    value=${value//$'\r'/}
    printf '%s' "$value"
}

validate_admin_password() {
    local env_file="$1"
    local min_length="${2:-6}"

    local admin_password
    admin_password=$(get_env_value "$env_file" "ADMIN_PASSWORD")

    # Allow empty: empty means no admin account initialization (for OAuth-only login scenarios)
    if [ -z "$admin_password" ]; then
        return 0
    fi

    if [ "${#admin_password}" -lt "$min_length" ]; then
        log_error "ADMIN_PASSWORD must be at least ${min_length} characters (currently ${#admin_password} characters), otherwise the backend will throw a validation error / login will fail"
        log_error "Please update ADMIN_PASSWORD in ${env_file} and try again"
        return 1
    fi

    return 0
}

# Fix docker directory permissions (resolves permission issues on NAS and similar environments)
fix_permissions() {
    log_info "Fixing docker directory permissions..."

    # Only process the docker directory to avoid accidentally changing .env / other repo file permissions
    TARGET_DIR="$SCRIPT_DIR/docker"
    if [ ! -d "$TARGET_DIR" ]; then
        log_warn "Docker directory not found, skipping permission fix"
        return 0
    fi

    # Set directories to 755 (rwxr-xr-x)
    find "$TARGET_DIR" -type d -exec chmod 755 {} \; 2>/dev/null || true

    # Set regular files to 644 (rw-r--r--)
    find "$TARGET_DIR" -type f -exec chmod 644 {} \; 2>/dev/null || true

    # Set script files to 755 (rwxr-xr-x)
    find "$TARGET_DIR" -name "*.sh" -type f -exec chmod 755 {} \; 2>/dev/null || true

    log_info "Docker directory permissions fixed"
}

# Initialize compose environment (shared by deploy/upgrade/uninstall)
prepare_compose() {
    if [ ! -f docker-compose.yml ]; then
        log_error "docker-compose.yml not found. Please run this script from the project root directory"
        exit 1
    fi

    # Check dependencies
    log_info "Checking system dependencies..."
    check_command docker

    # Detect docker compose command (prefer the newer version)
    if docker compose version &> /dev/null; then
        DOCKER_COMPOSE="docker compose"
    elif command -v docker-compose &> /dev/null; then
        DOCKER_COMPOSE="docker-compose"
    else
        log_error "Neither docker-compose nor docker compose is installed"
        exit 1
    fi
    log_info "Using command: $DOCKER_COMPOSE"

    # Compose docker compose files: base compose (web/backend/postgres/redis)
    COMPOSE_FILES="-f docker-compose.yml"

    compose() {
        $DOCKER_COMPOSE $COMPOSE_FILES "$@"
    }

    # Check if Docker is running
    if ! docker info &> /dev/null; then
        log_error "Docker is not running. Please start the Docker service first"
        exit 1
    fi
}

# Deploy (first-time deployment / reinstall)
deploy() {
    log_info "Starting AntiHub-ALL deployment..."
    echo ""

    # 0. Fix permissions (resolves permission issues on NAS and similar environments)
    fix_permissions

    # 1. Initialize compose environment
    prepare_compose
    if ! command -v openssl &> /dev/null; then
        log_warn "openssl is not installed. Random keys will be generated via Docker (may pull python:3.11-alpine image)"
    fi

    # 2. Check .env file
    ENV_BACKUP_FILE=""
    if [ -f .env ]; then
        log_warn ".env file already exists"
        read -p "Overwrite existing configuration? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Keeping existing configuration, skipping environment variable generation"
            ENV_EXISTS=true
        else
            ENV_EXISTS=false
            ENV_BACKUP_FILE=".env.bak.$(date +\"%Y%m%d_%H%M%S\")"
            cp .env "$ENV_BACKUP_FILE"
            log_info "Backed up original .env to ${ENV_BACKUP_FILE}"
        fi
    else
        ENV_EXISTS=false
    fi

    # 3. Generate environment variable configuration
    if [ "$ENV_EXISTS" = false ]; then
        log_info "Starting deployment parameter configuration..."
        echo ""

        if [ ! -f .env.example ]; then
            log_error ".env.example file not found"
            exit 1
        fi

        cp .env.example .env

        # 3.1 Configure ports
        log_info "=== Port Configuration ==="
        log_prompt "Configure service ports (press Enter to use defaults)"
        echo ""

        WEB_PORT=$(read_with_default "Web frontend port (externally exposed)" "3000")
        BACKEND_PORT=$(read_with_default "Backend port (local only)" "8000")
        POSTGRES_PORT=$(read_with_default "PostgreSQL database port (local only)" "5432")

        echo ""
        log_info "Port configuration complete:"
        echo "  Web: $WEB_PORT (0.0.0.0:$WEB_PORT)"
        echo "  Backend: $BACKEND_PORT (127.0.0.1:$BACKEND_PORT)"
        echo "  PostgreSQL: $POSTGRES_PORT (127.0.0.1:$POSTGRES_PORT)"
        echo ""

        # 3.2 Access method (affects the Secure attribute of login cookies)
        log_info "=== Access Method (affects login cookies) ==="
        echo "Select how you plan to access the frontend:"
        echo "  1) Domain + HTTPS (recommended, cookies will have Secure flag)"
        echo "  2) Direct IP + HTTP (testing/intranet, cookies without Secure flag)"

        COOKIE_HTTP="HTTPS"
        DOMAIN_NAME=""
        while true; do
            read -p "Select [1-2] (default 1): " access_choice
            access_choice=${access_choice//$'\r'/}
            access_choice=${access_choice:-1}

            case "$access_choice" in
                1)
                    COOKIE_HTTP="HTTPS"
                    read -p "Domain name (optional, only used for post-deployment info display; leave empty to skip): " DOMAIN_NAME
                    DOMAIN_NAME=${DOMAIN_NAME//$'\r'/}
                    ;;
                2)
                    COOKIE_HTTP="HTTP"
                    DOMAIN_NAME=""
                    ;;
                *)
                    log_warn "Invalid selection, please enter 1 or 2"
                    continue
                    ;;
            esac

            break
        done

        echo ""
        log_info "Access method configured: COOKIE_HTTP=$COOKIE_HTTP"
        if [ -n "$DOMAIN_NAME" ]; then
            echo "  Domain: $DOMAIN_NAME"
        fi
        echo ""

        # 3.3 Configure admin account
        log_info "=== Admin Account Configuration ==="
        ADMIN_USERNAME=$(read_with_default "Admin username" "admin")
        log_prompt "Set admin password (minimum 6 characters, otherwise login will fail)"
        ADMIN_PASSWORD=$(read_password "Admin password" 6)
        echo ""
        log_info "Admin account configuration complete"
        echo ""

        # 3.4 Generate secret keys
        log_info "Generating security keys..."
        OLD_JWT_SECRET=$(get_env_value "$ENV_BACKUP_FILE" "JWT_SECRET_KEY")
        OLD_POSTGRES_PASSWORD=$(get_env_value "$ENV_BACKUP_FILE" "POSTGRES_PASSWORD")
        OLD_ENCRYPTION_KEY=$(get_env_value "$ENV_BACKUP_FILE" "PLUGIN_API_ENCRYPTION_KEY")

        if [ -n "$OLD_JWT_SECRET" ] && [ "$OLD_JWT_SECRET" != "please-change-me" ]; then
            JWT_SECRET="$OLD_JWT_SECRET"
        else
            JWT_SECRET=$(generate_random_key)
        fi

        if [ -n "$OLD_POSTGRES_PASSWORD" ] && [ "$OLD_POSTGRES_PASSWORD" != "please-change-me" ]; then
            POSTGRES_PASSWORD="$OLD_POSTGRES_PASSWORD"
        else
            POSTGRES_PASSWORD=$(generate_random_key | cut -c1-24)
        fi

        log_info "Generating Fernet encryption key..."
        if [ -n "$OLD_ENCRYPTION_KEY" ] && [ "$OLD_ENCRYPTION_KEY" != "please-generate-a-valid-fernet-key" ]; then
            ENCRYPTION_KEY="$OLD_ENCRYPTION_KEY"
        else
            ENCRYPTION_KEY=$(generate_fernet_key)
        fi

        # 3.5 Replace placeholders in .env (compatible with Linux and macOS)
        log_info "Writing configuration file..."
        write_env_file ".env"

        log_info "Environment variable configuration generated"
        echo ""
    fi

    # 3.6 Validate critical configuration (avoid discovering login/validation errors only after startup)
    validate_admin_password ".env" 6

    # 4. Pull images
    log_info "Pulling Docker images..."
    compose pull

    # 5. Stop old containers (if any)
    log_info "Stopping old containers..."
    compose down 2>/dev/null || true

    # 6. Start base dependencies (database/cache) first, complete DB initialization, then start main containers
    log_info "Starting database and cache (postgres/redis)..."
    compose up -d postgres redis

    log_info "Checking PostgreSQL status..."
    POSTGRES_USER_CHECK=$(grep "^POSTGRES_USER=" .env | cut -d'=' -f2 || echo "antihub")
    for i in {1..30}; do
        if compose exec -T postgres pg_isready -U "$POSTGRES_USER_CHECK" &> /dev/null; then
            log_info "PostgreSQL is ready"
            break
        fi
        if [ $i -eq 30 ]; then
            log_error "PostgreSQL startup timed out"
            exit 1
        fi
        sleep 2
    done

    POSTGRES_USER_ENV=$(grep "^POSTGRES_USER=" .env | cut -d'=' -f2 || echo "antihub")
    POSTGRES_PASSWORD_ENV=$(grep "^POSTGRES_PASSWORD=" .env | cut -d'=' -f2- || echo "please-change-me")
    POSTGRES_DB_ENV=$(grep "^POSTGRES_DB=" .env | cut -d'=' -f2 || echo "antihub")
    # Initialize/sync database (Backend main database)
    log_info "Initializing database (${POSTGRES_DB_ENV})..."

    compose exec -T postgres psql -X -v ON_ERROR_STOP=1 \
        -U "$POSTGRES_USER_ENV" -d postgres \
        -v su_user="$POSTGRES_USER_ENV" -v su_pass="$POSTGRES_PASSWORD_ENV" \
        -v main_db="$POSTGRES_DB_ENV" <<-'EOSQL'
SELECT format('ALTER USER %I WITH PASSWORD %L', :'su_user', :'su_pass') \gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'main_db', :'su_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'main_db') \gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'main_db', :'su_user')
WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = :'main_db') \gexec
EOSQL

    log_info "Starting main services (backend/web)..."
    compose up -d backend web

    # Check service status
    log_info "Checking service status..."
    sleep 3

    FAILED_SERVICES=$(compose ps --services --filter "status=exited")
    if [ -n "$FAILED_SERVICES" ]; then
        log_error "The following services failed to start:"
        echo "$FAILED_SERVICES"
        log_info "Viewing logs:"
        compose logs --tail=50
        exit 1
    fi

    # 8. Output deployment information
    echo ""
    log_info "=========================================="
    log_info "AntiHub-ALL deployment complete!"
    log_info "=========================================="
    echo ""

    # Read port configuration
    WEB_PORT=$(grep "^WEB_PORT=" .env | cut -d'=' -f2 || echo "3000")
    BACKEND_PORT=$(grep "^BACKEND_PORT=" .env | cut -d'=' -f2 || echo "8000")
    POSTGRES_PORT=$(grep "^POSTGRES_PORT=" .env | cut -d'=' -f2 || echo "5432")
    POSTGRES_DB=$(grep "^POSTGRES_DB=" .env | cut -d'=' -f2 || echo "antihub")
    POSTGRES_DB=${POSTGRES_DB//$'\r'/}
    ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" .env | cut -d'=' -f2 || echo "admin")
    ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" .env | cut -d'=' -f2-)
    COOKIE_HTTP=$(grep "^COOKIE_HTTP=" .env | cut -d'=' -f2 || echo "HTTPS")
    COOKIE_HTTP=${COOKIE_HTTP//$'\r'/}
    COOKIE_HTTP_UPPER=$(echo "$COOKIE_HTTP" | tr '[:lower:]' '[:upper:]')

    # Get server IP
    SERVER_IP=$(hostname -I | awk '{print $1}' || echo "YOUR_SERVER_IP")

    log_info "Access URLs:"
    if [ "$COOKIE_HTTP_UPPER" = "HTTPS" ]; then
        if [ -n "$DOMAIN_NAME" ]; then
            echo "  Frontend (HTTPS): https://${DOMAIN_NAME}"
        else
            echo "  Frontend (HTTPS): https://<your-domain>"
        fi
        echo "  Web upstream (for reverse proxy): http://127.0.0.1:${WEB_PORT}"
    else
        echo "  Frontend (external): http://${SERVER_IP}:${WEB_PORT}"
        echo "  Frontend (local): http://localhost:${WEB_PORT}"
    fi
    echo "  Backend (local only): http://127.0.0.1:${BACKEND_PORT}"
    echo "  Cookie mode: ${COOKIE_HTTP_UPPER} (HTTP = no Secure flag; HTTPS = Secure flag enabled)"
    echo ""
    log_info "Reverse proxy notes (must read):"
    echo "  You must forward /backend to the backend, otherwise frontend requests will return 404"
    echo "  /        -> http://127.0.0.1:${WEB_PORT}"
    echo "  /backend -> http://127.0.0.1:${BACKEND_PORT}"
    echo ""
    log_info "Admin account:"
    echo "  Username: ${ADMIN_USERNAME}"
    echo "  Password: ${ADMIN_PASSWORD}"
    echo ""
    log_info "Database info (local access only):"
    echo "  PostgreSQL: localhost:${POSTGRES_PORT}"
    echo "  Database: ${POSTGRES_DB}"
    echo ""
    log_info "Common commands:"
    echo "  View logs: $DOCKER_COMPOSE $COMPOSE_FILES logs -f"
    echo "  Stop services: $DOCKER_COMPOSE $COMPOSE_FILES down"
    echo "  Restart services: $DOCKER_COMPOSE $COMPOSE_FILES restart"
    echo "  View status: $DOCKER_COMPOSE $COMPOSE_FILES ps"
    echo ""
    log_warn "Important notes:"
    echo "  1. Keep the secret keys in the .env file safe"
    echo "  2. The Web port is externally exposed; consider configuring a firewall"
    echo "  3. Backend and database are local-access only (127.0.0.1)"
    echo "  4. Reverse proxy must be configured: /backend -> http://127.0.0.1:${BACKEND_PORT}"
    echo "  5. Use COOKIE_HTTP=HTTP for direct IP+HTTP access; keep COOKIE_HTTP=HTTPS for domain+HTTPS"
    echo ""
}

upgrade() {
    log_info "Starting AntiHub-ALL upgrade (web/backend only, database will not be modified)..."
    echo ""

    fix_permissions
    prepare_compose

    if [ ! -f .env ]; then
        log_warn ".env not found. This directory does not appear to have been deployed yet. Entering one-click deployment flow"
        deploy
        return 0
    fi

    validate_admin_password ".env" 6

    # Back up .env to prevent accidental changes or rollback difficulties
    ENV_BACKUP_FILE=".env.bak.upgrade.$(date +\"%Y%m%d_%H%M%S\")"
    cp .env "$ENV_BACKUP_FILE"
    log_info "Backed up .env to ${ENV_BACKUP_FILE}"

    log_info "Pulling latest Docker images (web/backend only)..."
    compose pull web backend

    log_info "Restarting services (web/backend only), not restarting postgres/redis..."
    compose up -d --no-deps web backend

    log_info "Checking service status..."
    sleep 3

    FAILED_SERVICES=$(compose ps --services --filter "status=exited" | grep -E "^(web|backend)$" || true)
    if [ -n "$FAILED_SERVICES" ]; then
        log_error "The following services failed to start (web/backend):"
        echo "$FAILED_SERVICES"
        log_info "Viewing logs:"
        compose logs --tail=80
        exit 1
    fi

    DB_SERVICES_EXITED=$(compose ps --services --filter "status=exited" | grep -E "^(postgres|redis)$" || true)
    if [ -n "$DB_SERVICES_EXITED" ]; then
        log_warn "Database/cache services detected as not running (this upgrade will not modify them):"
        echo "$DB_SERVICES_EXITED"
    fi

    log_info "Upgrade complete (database was not restarted/rebuilt)!"
    echo "  View status: $DOCKER_COMPOSE $COMPOSE_FILES ps"
    echo "  View logs: $DOCKER_COMPOSE $COMPOSE_FILES logs -f"
    echo ""
}

uninstall() {
    log_warn "About to uninstall AntiHub-ALL"
    echo ""

    prepare_compose

    log_warn "Uninstall will stop and remove containers/networks; optionally delete data volumes (will erase database data)"
    read -p "Also delete data volumes (database/cache)? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_warn "Deleting data volumes (irreversible)..."
        compose down -v --remove-orphans 2>/dev/null || true
    else
        log_info "Keeping data volumes..."
        compose down --remove-orphans 2>/dev/null || true
    fi

    if [ -f .env ]; then
        read -p "Delete local .env configuration file? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -f .env
            log_info ".env deleted"
        else
            log_info "Keeping .env"
        fi
    fi

    log_info "Uninstall complete"
    echo ""
}

show_menu() {
    echo ""
    log_info "Select an operation:"
    echo "  1) One-click deploy (first-time deployment / reinstall)"
    echo "  2) Upgrade (web/backend only, database will not be modified)"
    echo "  3) Uninstall (stop and remove containers, optionally delete data volumes)"
    echo "  0) Exit"
    echo ""

    while true; do
        read -p "Enter selection [0-3]: " choice
        choice=${choice//$'\r'/}
        case "$choice" in
            1) deploy; break ;;
            2) upgrade; break ;;
            3) uninstall; break ;;
            0) log_info "Exited"; exit 0 ;;
            *) log_warn "Invalid selection, please enter 0/1/2/3" ;;
        esac
    done
}

case "${1:-}" in
    1|deploy|install)
        deploy
        ;;
    2|upgrade|update)
        upgrade
        ;;
    3|uninstall|remove)
        uninstall
        ;;
    -h|--help|help)
        echo "Usage: ./deploy-en.sh [deploy|upgrade|uninstall]"
        echo "  deploy     One-click deploy (first-time deployment / reinstall)"
        echo "  upgrade    Upgrade (web/backend only, database will not be modified)"
        echo "  uninstall  Uninstall (stop and remove containers, optionally delete data volumes)"
        echo ""
        echo "Run without arguments to enter the interactive menu."
        ;;
    "")
        show_menu
        ;;
    *)
        log_warn "Unknown argument: $1"
        echo "Available arguments: deploy | upgrade | uninstall | --help"
        echo "Or run without arguments to enter the interactive menu."
        exit 1
        ;;
esac
