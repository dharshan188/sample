#!/usr/bin/env bash
set -euo pipefail

# deploy.sh - creates venv, installs deps, creates .env, systemd + nginx service for NutriGuard
# Usage:
#   ./deploy.sh YOUR_GEMINI_API_KEY
# or
#   GEMINI_API_KEY=YOUR_KEY ./deploy.sh
#
# Run from project root (/home/dharshan/jeevith)

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="nutri"
SOCKET_PATH="$PROJECT_DIR/${SERVICE_NAME}.sock"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}"

# Accept key from arg or env
ARG_KEY="${1:-}"
GEMINI_API_KEY="${ARG_KEY:-${GEMINI_API_KEY:-}}"

if [[ -z "$GEMINI_API_KEY" ]]; then
  echo "ERROR: Provide GEMINI_API_KEY as first argument or as environment variable GEMINI_API_KEY."
  echo "Example: GEMINI_API_KEY=sk_... ./deploy.sh"
  exit 1
fi

echo "PROJECT_DIR: $PROJECT_DIR"
echo "VENV_DIR: $VENV_DIR"

# 1) Create venv
if [[ -d "$VENV_DIR" ]]; then
  echo "Reusing existing venv at $VENV_DIR"
else
  python3 -m venv "$VENV_DIR"
fi

# Activate pip from venv (non-interactive)
"$VENV_DIR/bin/pip" install --upgrade pip

# Write minimal requirements file
cat > "$PROJECT_DIR/requirements-min.txt" <<'REQ'
Flask==3.1.2
requests
gunicorn
python-dotenv
REQ

# Install minimal + google-genai
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-min.txt"
"$VENV_DIR/bin/pip" install google-genai

# 2) Write .env (do not overwrite if exists unless user confirms)
if [[ -f "$ENV_FILE" ]]; then
  echo ".env already exists at $ENV_FILE (will overwrite)."
fi

cat > "$ENV_FILE" <<EOF
USDA_API_KEY="QL9h3HBbQJyQhCA2DNXQnERfFXeKMxTOOUXYj1cu"
WEATHER_API_KEY="396de8bb5145446a9ee92656250311"
GEMINI_API_KEY="${GEMINI_API_KEY}"
FLASK_ENV=production
EOF
chmod 600 "$ENV_FILE"
echo "Wrote $ENV_FILE (mode 600)."

# 3) Quick gunicorn test (background): test socket creation
echo "Testing gunicorn startup (short test)..."
"$VENV_DIR/bin/gunicorn" --chdir "$PROJECT_DIR" --bind "unix:$SOCKET_PATH" --workers 1 app:app &
GTEST_PID=$!
sleep 2
if [[ -S "$SOCKET_PATH" ]]; then
  echo "Gunicorn test created socket: $SOCKET_PATH"
  kill "$GTEST_PID" || true
  sleep 1
  rm -f "$SOCKET_PATH"
else
  echo "ERROR: Gunicorn test did not create socket. Check logs. Killing test pid $GTEST_PID"
  kill "$GTEST_PID" || true
  exit 1
fi

# 4) Create systemd unit (requires sudo)
echo "Creating systemd unit at $SYSTEMD_FILE..."
sudo tee "$SYSTEMD_FILE" > /dev/null <<EOF
[Unit]
Description=NutriGuard (Gunicorn)
After=network.target

[Service]
User=$(whoami)
Group=$(id -gn)
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment=PATH=$VENV_DIR/bin
ExecStart=$VENV_DIR/bin/gunicorn --chdir $PROJECT_DIR --workers 3 --bind unix:$SOCKET_PATH --timeout 120 app:app
Restart=on-failure
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd..."
sudo systemctl daemon-reload
echo "Starting service..."
sudo systemctl start "$SERVICE_NAME"
sleep 1
sudo systemctl status "$SERVICE_NAME" --no-pager

# 5) Create nginx site and enable it
echo "Installing nginx (if missing) and creating site..."
sudo apt-get update -y
sudo apt-get install -y nginx

sudo tee "$NGINX_SITE" > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    location / {
        include proxy_params;
        proxy_pass http://unix:REPLACE_SOCKET;
    }

    location /static/ {
        alias REPLACE_PROJECT/static/;
        expires 1d;
        add_header Cache-Control "public";
    }

    location /health {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
EOF

# replace placeholders
sudo sed -i "s|REPLACE_SOCKET|$SOCKET_PATH|g" "$NGINX_SITE"
sudo sed -i "s|REPLACE_PROJECT|$PROJECT_DIR|g" "$NGINX_SITE"

sudo ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/"$SERVICE_NAME"
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager

# 6) Fix socket ownership (so nginx (www-data) can access it)
sleep 1
if [[ -S "$SOCKET_PATH" ]]; then
  if getent passwd www-data >/dev/null; then
    sudo chown www-data:www-data "$SOCKET_PATH" || true
    sudo chmod 660 "$SOCKET_PATH" || true
    echo "Socket ownership set to www-data:www-data"
  else
    echo "www-data not found; leaving ownership as current user (you may need to adjust nginx or run gunicorn as www-data)."
  fi
else
  echo "WARNING: socket not present. Check 'sudo journalctl -u $SERVICE_NAME -n 200'."
fi

# 7) Enable service at boot
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "DEPLOY COMPLETE."
echo "Open http://localhost in a browser to test."
echo ""
echo "If something's wrong, run:"
echo "  sudo journalctl -u $SERVICE_NAME -n 200 --no-pager"
echo "  sudo tail -n 200 /var/log/nginx/error.log"
echo "  ls -l $SOCKET_PATH"
echo ""
echo "Quick test (curl examples):"
echo "1) Analyze (example):"
echo "curl -s -X POST http://localhost/analyze -H 'Content-Type: application/json' -d '{\"city\":\"coimbatore\",\"gender\":\"male\",\"height\":170,\"weight\":70,\"items\":[{\"name\":\"rice\",\"qty\":200}]}' | jq"
echo ""
echo "2) Consult with Gemini (example):"
echo "curl -s -X POST http://localhost/consult -H 'Content-Type: application/json' -d '{\"gender\":\"male\",\"height\":170,\"weight\":70,\"age\":28,\"lang\":\"en\",\"total_nutrients\":{},\"deficient\":{},\"weather\":{\"condition\":\"Clear\",\"temp\":28,\"humidity\":40}}' | jq"
