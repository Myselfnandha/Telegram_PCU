#!/usr/bin/env bash
# ==============================================================================
# TG Power Suite — Release Packaging Script
# Generates clean, self-contained release distributions (.tar.gz and .zip)
# ==============================================================================

set -e

VERSION="v3.2.5"
DIST_DIR="dist"
PACKAGE_NAME="tg-power-suite-${VERSION}-universal"
TARGET_DIR="${DIST_DIR}/${PACKAGE_NAME}"

echo "📦 Packaging TG Power Suite ${VERSION}..."

# 1. Pre-bundle JavaScript for zero-dependency standalone execution
if command -v npx >/dev/null 2>&1; then
    echo "→ Pre-compiling standalone frontend bundle with esbuild..."
    npx -y esbuild frontend/js/app.js --bundle --outfile=frontend/js/app.bundle.js --format=iife || true
fi

rm -rf "${DIST_DIR}"
mkdir -p "${TARGET_DIR}"

# 2. Copy Core Engine, UI & Platform Launchers
echo "→ Copying backend, frontend, assets, platform launchers and documentation..."
cp -r backend "${TARGET_DIR}/"
cp -r frontend "${TARGET_DIR}/"
cp -r assets "${TARGET_DIR}/"
cp -r linux "${TARGET_DIR}/"
cp -r windows "${TARGET_DIR}/"
cp -r scripts "${TARGET_DIR}/"
cp Dockerfile "${TARGET_DIR}/"
cp docker-compose.yml "${TARGET_DIR}/"
cp README.md "${TARGET_DIR}/"
cp .gitignore "${TARGET_DIR}/"

# 3. Privacy & Cache Cleaning (Never leak sessions, tokens, DBs, or temporary files)
echo "→ Cleaning cache, session tokens, and local artifacts..."
find "${TARGET_DIR}" -type d -name "__pycache__" -exec rm -rf {} +
find "${TARGET_DIR}" -type d -name ".pytest_cache" -exec rm -rf {} +
find "${TARGET_DIR}" -type d -name ".vscode" -exec rm -rf {} +
find "${TARGET_DIR}" -name "*.pyc" -delete
find "${TARGET_DIR}" -name "*.session" -delete
find "${TARGET_DIR}" -name "*.session-journal" -delete
find "${TARGET_DIR}" -name "*.db" -delete
find "${TARGET_DIR}" -name "*.db-wal" -delete
find "${TARGET_DIR}" -name "*.db-shm" -delete
find "${TARGET_DIR}" -name ".env" -delete
rm -rf "${TARGET_DIR}/backend/downloads/"*
rm -rf "${TARGET_DIR}/backend/temp_uploads/"*

# 4. Create Tarball & Zip Distributions
cd "${DIST_DIR}"
echo "→ Compressing .tar.gz archive..."
tar -czvf "${PACKAGE_NAME}.tar.gz" "${PACKAGE_NAME}" > /dev/null

echo "→ Compressing .zip archive..."
python3 -c "import shutil; shutil.make_archive('${PACKAGE_NAME}', 'zip', '.', '${PACKAGE_NAME}')"

echo ""
echo "✅ Release packages successfully created in ${DIST_DIR}/:"
ls -lh *.tar.gz *.zip

cd ..
