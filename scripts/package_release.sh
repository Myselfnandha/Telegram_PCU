#!/usr/bin/env bash
# ==============================================================================
# TG Power Suite — Release Packaging Script
# Generates self-contained release distributions (.tar.gz and .zip)
# ==============================================================================

set -e

VERSION="v2.7.0"
DIST_DIR="dist"
PACKAGE_NAME="tg-power-suite-${VERSION}-linux-x64"
TARGET_DIR="${DIST_DIR}/${PACKAGE_NAME}"

echo "📦 Packaging TG Power Suite ${VERSION}..."

rm -rf "${DIST_DIR}"
mkdir -p "${TARGET_DIR}"

# Copy Core Engine & Web UI
echo "→ Copying backend, frontend, assets and scripts..."
cp -r backend "${TARGET_DIR}/"
cp -r frontend "${TARGET_DIR}/"
cp -r assets "${TARGET_DIR}/"
cp -r linux "${TARGET_DIR}/"
cp -r windows "${TARGET_DIR}/"
cp Dockerfile "${TARGET_DIR}/"
cp docker-compose.yml "${TARGET_DIR}/"
cp README.md "${TARGET_DIR}/"
cp .gitignore "${TARGET_DIR}/"

# Clean any cache junk
find "${TARGET_DIR}" -type d -name "__pycache__" -exec rm -rf {} +
find "${TARGET_DIR}" -type d -name ".pytest_cache" -exec rm -rf {} +
find "${TARGET_DIR}" -name "*.pyc" -delete

# Create Tarball & Zip
cd "${DIST_DIR}"
echo "→ Compressing .tar.gz archive..."
tar -czvf "${PACKAGE_NAME}.tar.gz" "${PACKAGE_NAME}" > /dev/null

echo "→ Compressing .zip archive with Python..."
python3 -c "import shutil; shutil.make_archive('${PACKAGE_NAME}', 'zip', '.', '${PACKAGE_NAME}')"

echo ""
echo "✅ Release packages successfully created in ${DIST_DIR}/:"
ls -lh *.tar.gz *.zip

cd ..
