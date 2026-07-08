# Liveness Probe Failure

## Symptoms

- Repeated liveness probe failures
- Restarts
- Application may respond quickly when manually tested

## Read-only checks

- Describe pod events
- Check probe timeout and failureThreshold
- Check recent logs around probe failures
- Check JVM pauses if available
- Check CPU throttling and memory pressure
- Check TLS/connection backlog symptoms

## Risky actions

- Increasing probe timeout without root cause
- Restarting pods
- Changing deployment config

These require human approval.
