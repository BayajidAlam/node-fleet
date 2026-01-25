#!/bin/bash
set -e

echo "Building Lambda deployment package..."

# Create temp directory
BUILD_DIR="/tmp/lambda-build-$$"
mkdir -p "$BUILD_DIR"

# Copy only essential Python files (exclude kubernetes, tests, pycache, venv)
cd ../lambda
rsync -av --exclude='kubernetes/' \
          --exclude='__pycache__/' \
          --exclude='*.pyc' \
          --exclude='.pytest_cache/' \
          --exclude='tests/' \
          --exclude='*.egg-info/' \
          --exclude='venv/' \
          ./ "$BUILD_DIR/"

# Create zip
cd "$BUILD_DIR"
zip -r /tmp/lambda-deployment.zip . -q

echo "Lambda package created: /tmp/lambda-deployment.zip"
ls -lh /tmp/lambda-deployment.zip

# Cleanup
rm -rf "$BUILD_DIR"
