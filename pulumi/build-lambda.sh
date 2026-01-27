#!/bin/bash
set -e

echo "Building Lambda deployment package..."

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Create temp directory
BUILD_DIR="/tmp/lambda-build-$$"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Selective copy: Only core Python logic and modules
# This avoids pulling in local dependency remnants (botocore, numpy, etc.)
echo "Copying core Lambda logic..."
cp "$PROJECT_ROOT/lambda/"*.py "$BUILD_DIR/"
if [ -d "$PROJECT_ROOT/lambda/modules" ]; then
    cp -r "$PROJECT_ROOT/lambda/modules" "$BUILD_DIR/"
fi

# Install dependencies into the build directory
echo "Installing dependencies via wheel extraction..."
WHEEL_DIR="/tmp/lambda-wheels-$$"
mkdir -p "$WHEEL_DIR"

# Download dependencies
echo "   • Downloading all dependencies from requirements.txt..."
# We download everything first to get transient dependencies (charset-normalizer, urllib3, etc.)
pip download -r "$PROJECT_ROOT/lambda/requirements.txt" -d "$WHEEL_DIR" \
    --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 \
    --only-binary=:all: --quiet

# Specifically overwrite cryptography with the no-rust version for ultimate stability
echo "   • Applying cryptography 3.3.2 (No-Rust override)..."
rm -f "$WHEEL_DIR/cryptography"*
pip download cryptography==3.3.2 -d "$WHEEL_DIR" \
    --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 \
    --only-binary=:all: --no-deps --quiet

# Extract each wheel into the build directory
for wheel in "$WHEEL_DIR"/*.whl; do
    echo "   • Extracting $(basename "$wheel")..."
    unzip -qo "$wheel" -d "$BUILD_DIR/"
done
rm -rf "$WHEEL_DIR"

# Aggressive pruning to reduce package size
echo "Pruning unnecessary files from build directory..."
find "$BUILD_DIR" -type d -name "tests" -exec rm -rf {} +
find "$BUILD_DIR" -type d -name "test" -exec rm -rf {} +
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} +
find "$BUILD_DIR" -name "*.pyc" -delete
find "$BUILD_DIR" -name "*.egg-info" -exec rm -rf {} +
find "$BUILD_DIR" -name "*.dist-info" -exec rm -rf {} +
rm -rf "$BUILD_DIR/boto3" "$BUILD_DIR/botocore" "$BUILD_DIR/s3transfer" # Just in case they got pulled in

# Create zip
echo "Creating deployment package..."
cd "$BUILD_DIR"
rm -f /tmp/lambda-deployment.zip
zip -r /tmp/lambda-deployment.zip . -q

echo "Lambda package created: /tmp/lambda-deployment.zip"
ls -lh /tmp/lambda-deployment.zip

# Cleanup
# rm -rf "$BUILD_DIR"
