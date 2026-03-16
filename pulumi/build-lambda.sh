#!/bin/bash
set -e

echo "Building Lambda deployment package..."

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Create temp directory (use local path to avoid Windows /tmp mapping issues)
BUILD_DIR="$SCRIPT_DIR/.lambda-build-$$"
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
WHEEL_DIR="$SCRIPT_DIR/.lambda-wheels-$$"
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

OUTPUT_ZIP="$SCRIPT_DIR/lambda-deployment.zip"

# Create zip
echo "Creating deployment package..."
cd "$BUILD_DIR"
rm -f "$OUTPUT_ZIP"
zip -r "$OUTPUT_ZIP" . -q

echo "Lambda package created: $OUTPUT_ZIP"
ls -lh "$OUTPUT_ZIP"

# Cleanup
rm -rf "$BUILD_DIR"

echo ""
echo "To deploy:"
echo "  BUCKET=node-fleet-cluster-lambda-artifacts-dev"
echo "  aws s3 cp $OUTPUT_ZIP s3://\$BUCKET/lambda-deployment.zip --region ap-southeast-1"
echo "  aws lambda update-function-code --function-name node-fleet-cluster-autoscaler --s3-bucket \$BUCKET --s3-key lambda-deployment.zip --region ap-southeast-1"
