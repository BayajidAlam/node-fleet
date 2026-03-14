@echo off
cd D:\personal\all-projects\poridhi-project\node-fleet

echo === Step 1: Adding files ===
git add lambda/ec2_manager.py lambda/autoscaler.py lambda/scaling_decision.py lambda/state_manager.py pulumi/src/lambda.ts
if errorlevel 1 (
    echo Error in git add
    exit /b 1
)
echo Files added successfully
echo.

echo === Step 2: Showing cached diff ===
git diff --cached --stat
echo.

echo === Step 3: Committing changes ===
git commit -m "fix: resolve all 7 critical bugs from code review

- ec2_manager: replace hardcoded master IP (10.0.1.147) with dynamic _get_master_ip() resolving via EC2 tag Role=k3s-master
- ec2_manager: validate drain completion by checking exit code AND 'drained' keyword in kubectl output
- ec2_manager: fix spot interruption drain timeout 120s -> 300s for consistency with 5-minute requirement
- autoscaler: fix NameError - replace undefined PROMETHEUS_USERNAME/PROMETHEUS_PASSWORD with prom_user/prom_pass already in scope
- scaling_decision: fix scale-down window 10 -> 5 readings so 10min sustained check is correct at 2-min intervals
- state_manager: extend lock expiry 120s -> 360s to cover worst-case drain + join without stale lock races
- pulumi/lambda.ts: fix EventBridge schedule rate(1 minute) -> rate(2 minutes) per spec"
if errorlevel 1 (
    echo Error in git commit
    exit /b 1
)
echo Commit created successfully
echo.

echo === Step 4: Pushing to origin main ===
git push origin main
if errorlevel 1 (
    echo Error in git push
    exit /b 1
)
echo Push completed successfully
