#!/bin/bash
set -e

# Your patched fork URL
REPO_URL="https://github.com/Minfrog314/MyZimi.git"

echo "--- 1. Installing System Dependencies ---"
apt-get update
# Include dependencies for building Python 3.12 from source
apt-get install -y \
    bash curl jq git wget \
    ffmpeg imagemagick \
    build-essential libncursesw5-dev libssl-dev libsqlite3-dev tk-dev \
    libgdbm-dev libc6-dev libbz2-dev libffi-dev zlib1g-dev liblzma-dev \
    kiwix-tools aria2

echo "--- 2. Compiling Python 3.12 (Required by latest OpenZIM scrapers) ---"
cd /tmp
wget https://www.python.org/ftp/python/3.12.4/Python-3.12.4.tar.xz
tar -xf Python-3.12.4.tar.xz
cd Python-3.12.4
./configure --enable-optimizations
make -j$(nproc)
make altinstall
cd /
rm -rf /tmp/Python-3.12.4*

echo "--- 3. Cloning Repository ---"
mkdir -p /data/zims
git clone "$REPO_URL" /opt/zimi
cd /opt/zimi

echo "--- 4. Fixing fcc2zim Package Name Typo ---"
# The PyPI package and binary for FreeCodeCamp is actually 'fcc2zim'
sed -i 's/freecodecamp2zim/fcc2zim/g' zimi/scrapers.py
sed -i 's/freecodecamp2zim/fcc2zim/g' zimi/static/app.js

echo "--- 5. Setting up Python 3.12 Venv & Requirements ---"
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# Install Zimi's core requirements
pip install -r requirements.txt

# Install the lightweight Python scraper suite
pip install youtube2zim sotoki gutenberg2zim ted2zim devdocs2zim ifixit2zim wikihow2zim fcc2zim warc2zim

deactivate

echo "--- 6. Creating Systemd Service ---"
cat << 'EOF' > /etc/systemd/system/zimi.service
[Unit]
Description=Zimi Offline Reader & Scraper Engine
After=network.target

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

echo "--- 7. Starting Services ---"
systemctl daemon-reload
systemctl enable --now zimi

echo "========================================================"
echo " Native Lightweight Installation Complete!"
echo " Zimi is running on port 8080."
echo " ZIM directory is located at: /data/zims"
echo " Logs can be viewed via: journalctl -fu zimi"
echo "========================================================"
