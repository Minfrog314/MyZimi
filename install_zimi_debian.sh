#!/bin/bash
set -e

# Your patched fork URL
REPO_URL="https://github.com/Minfrog314/MyZimi.git"

echo "--- 1. Installing System Dependencies ---"
apt-get update
apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    bash curl jq git wget \
    ffmpeg imagemagick \
    build-essential libffi-dev zlib1g-dev libjpeg-dev \
    kiwix-tools aria2

echo "--- 2. Cloning Repository ---"
mkdir -p /data/zims
git clone "$REPO_URL" /opt/zimi
cd /opt/zimi

echo "--- 3. Setting up Python Venv & Requirements ---"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# Install Zimi's core requirements
pip install -r requirements.txt

# Install the lightweight Python scraper suite
pip install youtube2zim sotoki gutenberg2zim ted2zim devdocs2zim ifixit2zim wikihow2zim freecodecamp2zim warc2zim

deactivate

echo "--- 4. Creating Systemd Service ---"
cat << 'EOF' > /etc/systemd/system/zimi.service
[Unit]
Description=Zimi Offline Reader & Scraper Engine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/zimi
# Expose the venv so the subprocess orchestrator finds the scraper CLI tools
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
echo " Native Lightweight Installation Complete!"
echo " Zimi is running on port 8080."
echo " ZIM directory is located at: /data/zims"
echo " Logs can be viewed via: journalctl -fu zimi"
echo "========================================================"
