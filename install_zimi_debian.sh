#!/bin/bash
set -e

REPO_URL="https://github.com/Minfrog314/MyZimi.git"

echo "--- 1. Installing System Dependencies ---"
apt-get update
# We only need standard Debian packages + Docker
apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    bash curl jq git wget \
    ffmpeg imagemagick docker.io \
    kiwix-tools aria2

systemctl enable --now docker

echo "--- 2. Cloning Repository ---"
mkdir -p /data/zims
git clone "$REPO_URL" /opt/zimi
cd /opt/zimi

echo "--- 3. Setting up Zimi Core Environment ---"
# Uses Debian's lightning-fast native Python 3.11
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "--- 4. Creating Systemd Service ---"
cat << 'EOF' > /etc/systemd/system/zimi.service
[Unit]
Description=Zimi Offline Reader & Scraper Engine
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/zimi
Environment="PATH=/opt/zimi/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="ZIM_DIR=/data/zims"
ExecStart=/opt/zimi/venv/bin/python3 -m zimi serve --port 8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

echo "--- 5. Starting Services ---"
systemctl daemon-reload
systemctl enable --now zimi

echo "========================================================"
echo " Docker-Wrapped Installation Complete!"
echo " Zimi is running natively on port 8080."
echo "========================================================"
