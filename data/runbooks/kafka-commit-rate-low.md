# Kafka Consumer Commit Rate Low

## Symptoms

- KafkaConsumerCommitRateLow fires
- Commit rate below threshold
- Consumer may still be processing long messages

## Read-only checks

- Check consumer logs
- Check max.poll.interval.ms
- Check max.poll.records
- Confirm enable.auto.commit
- Compare alert for-window to max processing interval

## Common false positive

If auto commit is disabled and commitSync happens at the end of processing, long-running message processing can create low commit-rate periods.

## Risky actions

- Restarting consumers
- Changing consumer config in production

These require human approval.
