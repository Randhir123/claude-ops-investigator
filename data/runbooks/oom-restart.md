# OOM Restart Investigation

## Symptoms

- Pod restarts
- Last state shows OOMKilled
- Memory usage spikes near limit
- No gradual leak pattern necessarily present

## Read-only checks

- Get pod events
- Describe pod
- Check restart count
- Check container last state
- Inspect recent logs
- Compare memory request/limit with observed usage
- Check payload size or workload spikes if available

## Likely causes

- Large payload spike
- High rule fanout
- Batch processing burst
- Insufficient memory limit
- Heap pressure or native memory pressure

## Risky actions

- Restarting pods
- Scaling deployments
- Changing memory limits

These require human approval.
