#!/bin/bash
set -e

echo "Building Lambda deployment package..."

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Detect Python command (Windows Git Bash uses 'python', Linux uses 'python3')
if command -v python3 &>/dev/null && python3 --version 2>&1 | grep -q "Python 3"; then
  PYTHON=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
  PYTHON=python
else
  echo "❌ ERROR: Python 3 not found"
  exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version))"

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
$PYTHON -m pip download -r "$PROJECT_ROOT/lambda/requirements.txt" -d "$WHEEL_DIR" \
    --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 \
    --only-binary=:all: --quiet

# Specifically overwrite cryptography with the no-rust version for ultimate stability
echo "   • Applying cryptography 3.3.2 (No-Rust override)..."
rm -f "$WHEEL_DIR/cryptography"*
$PYTHON -m pip download cryptography==3.3.2 -d "$WHEEL_DIR" \
    --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 \
    --only-binary=:all: --no-deps --quiet

# Extract each wheel into the build directory
for wheel in "$WHEEL_DIR"/*.whl; do
    echo "   • Extracting $(basename "$wheel")..."
    $PYTHON -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$wheel" "$BUILD_DIR/"
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

# Create zip (use Python for Windows Git Bash compatibility)
echo "Creating deployment package..."
OUTPUT_ZIP="$SCRIPT_DIR/lambda-deployment.zip"
rm -f "$OUTPUT_ZIP"
$PYTHON -c "
import zipfile, os, sys
build_dir = sys.argv[1]
output = sys.argv[2]
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(build_dir):
        for file in files:
            filepath = os.path.join(root, file)
            arcname = os.path.relpath(filepath, build_dir)
            zf.write(filepath, arcname)
print('Done')
" "$BUILD_DIR" "$OUTPUT_ZIP"

echo "Lambda package created: $OUTPUT_ZIP"
ls -lh "$OUTPUT_ZIP"

# Cleanup
rm -rf "$BUILD_DIR"

echo ""
echo "To deploy:"
echo "  BUCKET=node-fleet-cluster-lambda-artifacts-dev"
echo "  aws s3 cp $OUTPUT_ZIP s3://\$BUCKET/lambda-deployment.zip --region ap-southeast-1"
echo "  aws lambda update-function-code --function-name node-fleet-cluster-autoscaler --s3-bucket \$BUCKET --s3-key lambda-deployment.zip --region ap-southeast-1"
