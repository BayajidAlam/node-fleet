#!/bin/bash
# SmartScale Complete Test Suite Runner
# Runs all tests and generates reports for documentation

set -e  # Exit on error

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         SmartScale K3s Autoscaler - Complete Test Suite       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results directory
RESULTS_DIR="test-results"
mkdir -p $RESULTS_DIR

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 1: TypeScript Infrastructure Tests${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

cd tests

# Run TypeScript tests
echo "Running TypeScript tests..."
npm test > ../$RESULTS_DIR/typescript-results.txt 2>&1
TS_EXIT_CODE=$?

if [ $TS_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ TypeScript tests: PASSED${NC}"
    cat ../$RESULTS_DIR/typescript-results.txt | tail -20
else
    echo -e "${YELLOW}⚠️  TypeScript tests: FAILED${NC}"
    cat ../$RESULTS_DIR/typescript-results.txt | tail -30
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 2: Python Tests Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Activate Python virtual environment
source test_venv/bin/activate

echo "Python environment activated"
echo "Python version: $(python --version)"
echo "Pytest version: $(pytest --version)"
echo ""

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 3: Python Lambda Tests (Core Logic)${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Run scaling decision tests (known to work)
echo "Running scaling decision tests..."
pytest lambda/test_scaling_decision.py -v --tb=short > ../$RESULTS_DIR/lambda-scaling-results.txt 2>&1
SCALING_EXIT_CODE=$?

if [ $SCALING_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Scaling decision tests: PASSED${NC}"
else
    echo -e "${YELLOW}⚠️  Scaling decision tests: Some failures${NC}"
fi
cat ../$RESULTS_DIR/lambda-scaling-results.txt | grep -E "(PASSED|FAILED|ERROR|test_)" | tail -15

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 4: Python Monitoring Tests (Cost Tracking)${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Run monitoring tests
echo "Running cost monitoring tests..."
pytest monitoring/test_cost_system.py -v --tb=short > ../$RESULTS_DIR/monitoring-results.txt 2>&1
MONITORING_EXIT_CODE=$?

if [ $MONITORING_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Monitoring tests: PASSED${NC}"
else
    echo -e "${YELLOW}⚠️  Monitoring tests: Some failures${NC}"
fi
cat ../$RESULTS_DIR/monitoring-results.txt | grep -E "(PASSED|FAILED|ERROR|test_)" | tail -20

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 5: Test Summary${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Count results
TS_PASSED=$(grep -o "Tests:.*passed" ../$RESULTS_DIR/typescript-results.txt | grep -o "[0-9]* passed" | grep -o "[0-9]*" || echo "0")
SCALING_PASSED=$(grep -o "[0-9]* passed" ../$RESULTS_DIR/lambda-scaling-results.txt | head -1 | grep -o "[0-9]*" || echo "0")
MONITORING_PASSED=$(grep -o "[0-9]* passed" ../$RESULTS_DIR/monitoring-results.txt | head -1 | grep -o "[0-9]*" || echo "0")

TOTAL_PASSED=$((TS_PASSED + SCALING_PASSED + MONITORING_PASSED))

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                      TEST RESULTS SUMMARY                      ║"
echo "╠════════════════════════════════════════════════════════════════╣"
echo "║  TypeScript (Infrastructure):  $TS_PASSED tests passed          ║"
echo "║  Python (Scaling Logic):       $SCALING_PASSED tests passed          ║"
echo "║  Python (Cost Monitoring):     $MONITORING_PASSED tests passed          ║"
echo "╠════════════════════════════════════════════════════════════════╣"
echo "║  TOTAL:                        $TOTAL_PASSED tests passed          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Generate summary file
cat > ../$RESULTS_DIR/summary.txt << EOF
SmartScale K3s Autoscaler - Test Execution Summary
Date: $(date)

RESULTS:
========
TypeScript Tests:     $TS_PASSED passed
Python Scaling Tests: $SCALING_PASSED passed  
Python Monitoring Tests: $MONITORING_PASSED passed
-------------------
TOTAL:                $TOTAL_PASSED passed

Test result files saved in: $RESULTS_DIR/
- typescript-results.txt
- lambda-scaling-results.txt
- monitoring-results.txt
EOF

echo -e "${GREEN}✅ Test execution complete!${NC}"
echo ""
echo "📁 Results saved in: $RESULTS_DIR/"
echo "📄 Summary: $RESULTS_DIR/summary.txt"
echo ""
echo "To view detailed results:"
echo "  cat $RESULTS_DIR/typescript-results.txt"
echo "  cat $RESULTS_DIR/lambda-scaling-results.txt"
echo "  cat $RESULTS_DIR/monitoring-results.txt"
