#!/bin/bash
# setup-dirs.sh — fix permissions on ./data and ./out directories
# Run this once after cloning the repository to ensure the podcaster service
# can write to these directories when running in Docker.

set -e

echo "Setting up data and output directories..."

# Create directories if they don't exist
mkdir -p ./data ./out

# Fix ownership and permissions for uid 1000 (the 'app' user inside the container)
sudo chown -R 1000:1000 ./data ./out
chmod -R u+rwX ./data ./out

echo "✓ Directories ready. You can now run: docker compose run --rm podcaster"
