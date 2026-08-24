#!/bin/bash
set -e

REPO_URL="https://github.com/Minfrog314/MyZimi.git"

echo "--- 1. Installing System Dependencies ---"
apt-get update
apt-get install -y \
    bash curl jq git wget \
    ffmpeg imagemagick \
    build-essential kiwix-tools aria2

echo "--- 2. Installing 'uv' (Python Toolchain Manager) ---"
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

echo "--- 3. Cloning Repository ---"
mkdir -p /data/zims
git clone "$REPO_URL" /opt/zimi
cd /opt/zimi

echo "--- 4. Fixing fcc2zim Package Name Typo ---"
sed -i 's/freecodecamp2zim/fcc2zim/g' zimi/scrapers.py
sed -i 's/freecodecamp2zim/fcc2zim/g' zimi/static/app.js

echo "--- 5. Setting up Zimi Core Environment ---"
uv venv /opt/zimi/venv
source /opt/zimi/venv/bin/activate
uv pip install -r requirements.txt
deactivate

echo "--- 6. Installing Isolated Scraper Tools via uv ---"
uv tool install youtube2zim
uv tool install sotoki
uv tool install gutenberg2zim
uv tool install ted2zim
uv tool install devdocs2zim
uv tool install ifixit2zim
uv tool install wikihow2zim
uv tool install fcc2zim
uv tool install warc2zim

echo "--- 7. Creating Systemd Service ---"
cat << 'INNER_EOF' > /etc/systemd/system/zimi.service
[Unit]
Description=Zimi Offline Reader & Scraper Engine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/zimi
Environment="PATH=/root/.local/bin:/opt/zimi/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="ZIM_DIR=/data/zims"
ExecStart=/opt/zimi/venv/bin/python3 -m zimi serve --port 8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
INNER_EOF

echo "--- 8. Starting Services ---"
systemctl daemon-reload
systemctl enable --now zimi

echo "========================================================"
echo " Native Lightweight Installation Complete!"
echo " Zimi is running on port 8080."
echo "========================================================"
