# Taking GC logs and thread dumps from OpenJ9 pods

How to get a JVM's **verbose GC log** and **thread dumps (javacores)** out of a
running pod, and have the agent analyze them. The Prometheus-based JVM tools
(`/investigate-jvm`) only show counts and 5-minute averages. These give you
every GC pause and every thread.

## Who does what

| Step | Who | Why |
|---|---|---|
| Decide which pod to look at | Agent (`/investigate-jvm`, `/investigate-incident`) or you | Read-only metrics |
| Read GC output from the container log | Agent (`jvm_get_gc_log_events`) | `kubectl logs` is read-only |
| Copy a GC log **file** or take a **thread dump** | **You**, with `scripts/capture-gclog.sh` / `scripts/capture-javacore.sh` | Needs `kubectl exec`, which agents never run |
| Analyze what you captured | Agent (`jvm_analyze_gc_log`, `jvm_analyze_javacore`, `jvm_compare_javacores`) | Reads local files under `runs/` only |

The capture scripts only work in **your own terminal**. They refuse to run
without an interactive terminal, so `! script…` inside Claude Code and Bob's
command runner won't work either. Claude Code's
`.claude/hooks/block_unsafe_shell.py` also blocks agents from running them.

**Before you start**
- `kubectl config current-context` points at the cluster the pod is in.
- Your identity is allowed to `exec` into pods in that namespace.
- You're in the repo root. The scripts save into `runs/`, which is gitignored.

## 1. Pick the pod

Run `/investigate-jvm namespace=<ns> service=<service>`. Its report names the
pods that stand out: long GC pauses, high GC overhead, outlier thread counts.
Or pick one yourself:

```bash
kubectl get pods -n si | grep time-series-query
```

## 2. GC logs

### Capture

```bash
scripts/capture-gclog.sh si <pod>              # newest 10 files
scripts/capture-gclog.sh -m 3 si <pod> [container]
```

The script asks the JVM itself where it writes GC output. It reads the JVM's
command line and its `OPENJ9_JAVA_OPTIONS` / `IBM_JAVA_OPTIONS` /
`JAVA_TOOL_OPTIONS` / `JDK_JAVA_OPTIONS`, then follows one of three cases:

| JVM is configured with | Where the GC log is | What happens |
|---|---|---|
| `-Xverbosegclog:<file>[,n,size]` or `-Xloggc:<file>` | A file. Relative paths are relative to the JVM's working directory (Liberty: `/opt/ibm/wlp/output/defaultServer`). | Copies it, plus rotated files, `%pid`/`%seq`/date patterns, newest first, into `runs/gclogs/<pod>-<utc>/` |
| `-verbose:gc` only | stderr | If the JVM's stderr is redirected to a file, copies that. Otherwise prints `command terminated with exit code 5` (expected) and tells you to let the agent read it with `jvm_get_gc_log_events` |
| Neither | Nowhere | Says so. OpenJ9 can't switch verbose GC on in a running JVM; it needs a `jvm.options` change and a restart |

Our Liberty images run with `-verbose:gc -verbose:sizes -Xloggc:logs/gc.log`,
so the log is a **file**:
`/opt/ibm/wlp/output/defaultServer/logs/gc.log`. Nothing shows up in
`kubectl logs`.

Typical output:

```
kubectl context : si-dev-vpc-dal-k8s-001/...
namespace       : si
pod             : time-series-query-57b6bd684f-k8xsl
max files       : 10 (newest first)

Reading the JVM's verbose GC log file(s) out of the pod. Nothing in the pod is changed.
Looking up the JVM's GC log configuration...
  JVM is configured with: -Xloggc:logs/gc.log
  copying /opt/ibm/wlp/output/defaultServer/logs/gc.log (9240576 bytes in the pod)
  copied 9240576 bytes

Saved 1 file(s) to runs/gclogs/time-series-query-57b6bd684f-k8xsl-20260930T234919Z
```

If it prints `WARNING: copied only X of Y bytes`, the transfer was cut short.
Run it again. The last record of a live log may be incomplete because the
JVM is mid-write; the analyzer ignores it.

### Analyze

Ask the agent:

> run `jvm_analyze_gc_log` on `runs/gclogs/time-series-query-57b6bd684f-k8xsl-20260930T234919Z`

You get:
- **Pause statistics:** count, p50/p95/p99/**true max**, broken down by
  scavenge vs global.
- **The longest pauses,** with timestamps and triggers: allocation failure,
  `System.gc()`, concurrent kickoff.
- **Problem events:** percolate (a scavenge that turned into a global GC) and
  `copy-failed` (nursery couldn't promote objects into tenure), plus GC
  warnings.

The result is archived as evidence with an `evidence_ref`. For very large
logs or visual analysis, open the files in IBM GCMV.

When the JVM logs to stderr instead, skip the capture and ask the agent to
`run jvm_get_gc_log_events for si/<pod>` (add `previous=true` after a restart).

## 3. Thread dumps (javacores)

### Capture

```bash
scripts/capture-javacore.sh si <pod>                 # one dump
scripts/capture-javacore.sh -n 3 -i 10 si <pod>      # 3 dumps, 10 s apart
```

How it works:
- **Trigger:** runs `jcmd <pid> Dump.java` in the container, falling back to
  `kill -3` (SIGQUIT) if `jcmd` doesn't produce a dump. The JVM keeps
  running; application threads pause briefly while the dump is written.
- **Locate:** uses the path `jcmd` reports, otherwise the JVM's own dump
  settings (`IBM_JAVACOREDIR`, `-Xdump:…directory=/file=`), its working
  directory, `logs/`, `/tmp` and `/opt/ibm`.
- **Copy:** streams each javacore to
  `runs/javacores/<pod>-<utc>[-n].txt`.

**When to take a series:** for a hang or slowdown. A thread sitting in the
same frame in every dump is **stuck**; one that moves between dumps is
**busy**. For a one-off look at thread pools, one dump is enough.

The javacores stay in the pod, usually as
`/opt/ibm/wlp/output/defaultServer/javacore.<date>.<time>.<pid>.<seq>.txt`, a
few MB each, until the pod restarts. See [Clean up](#4-clean-up) below. The
capture script and the agent both remind you.

### Analyze

Ask the agent:

> run `jvm_analyze_javacore` on `runs/javacores/<pod>-<utc>.txt`

You get:
- Threads by state.
- The largest thread pools (digits collapsed, e.g. `Default Executor-thread-#`).
- Deadlocks.
- Most-contended lock owners.
- Blocked and parked threads, with what they wait on.
- The most common stacks.
- Hot frames among runnable threads.

How to read it:
- **RUNNABLE isn't always busy.** Threads in `sun/nio/ch/EPoll.wait` are
  network selectors waiting for data (Cassandra driver `s#-io-#`, Netty).
  They're idle.
- **Large numbers in thread names mean churn.** For example,
  `tsdquery-rest-1-thread-1447506` means that pool has created 1.4 million
  threads since start. That usually points to an executor created per
  request or a very short keep-alive.
- **Deadlocks or many threads blocked on one owner** are the real red flags.
  The report lists them directly.

### Compare a series

One dump is a snapshot; a series shows what *changes*. Capture 3–5 dumps a
few seconds apart from the same pod, then ask the agent to compare them:

```bash
scripts/capture-javacore.sh -n 3 -i 10 si <pod>
```

> run `jvm_compare_javacores` on `runs/javacores/<pod>-<date>*.txt`

It matches each thread across dumps by its Java thread ID and reports:

| Field | Meaning | What to do |
|---|---|---|
| `stuck_runnable` | RUNNABLE in every dump with the same top 5 frames, grouped by stack | The prime suspects for a hang or a hot loop. Look at the top frame. |
| `blocked_throughout` | BLOCKED in every dump, with the lock and its owner | Follow the owner: what is *it* doing? |
| `idle_io_threads` | Same stack every time, but idle in `EPoll.wait`, `accept` and similar | Normal; network selectors waiting for data |
| `waiting_unchanged_threads` | Waiting or parked on the same thing in every dump | Normal for idle pool threads |
| `thread_churn` | Per pool: how far the thread-number suffix advanced, and threads created per second | A high rate means threads are created per task instead of reused |
| `threads_appeared` / `threads_disappeared` | Thread IDs present in the last dump but not the first, and vice versa | Short-lived threads; see churn |

Pick the interval to suit the question:

| Goal | Series |
|---|---|
| Find stuck threads during a stall or latency spike | `-n 5 -i 10`, *while it's happening* |
| Measure thread churn | `-n 3 -i 30`, any time |
| Baseline under normal load | `-n 3 -i 60` |

Each dump briefly pauses the JVM and leaves a file of a few MB in the pod, so
keep a series to 3–5 dumps.

For deeper analysis: `java -Xmx2g -jar ~/tools/tmda/jca.jar <javacore.txt>`
(IBM TMDA). The script prints this command too.

## 4. Clean up

Once you've analyzed the javacores, remove them from the pod:

```bash
scripts/cleanup-javacores.sh si <pod>         # the dumps capture-javacore.sh took from this pod
scripts/cleanup-javacores.sh -a si <pod>      # every javacore in the JVM's dump folders
```

- **By default** it removes exactly the javacores `capture-javacore.sh` took
  from that pod. The capture script records their paths in
  `runs/javacores/.in-pod/<namespace>_<pod>.txt`.
- **`-a`** finds every `javacore*.txt` in the JVM's dump folders. Use it for
  dumps taken before that record existed, or by hand. It also lists
  javacores the JVM wrote on its own (e.g. on an `OutOfMemoryError`), so
  check the list before saying yes.
- **It shows the files and sizes and asks once** before deleting. `-y` skips
  the question. It only ever deletes files named `javacore*.txt`, then checks
  they're gone.
- **Human-run only,** like the capture scripts. Agents tell you to run it but
  never run it themselves.

GC log captures only read files, so there's nothing to clean up after them.

## 5. Doing it by hand

If the scripts aren't available, these are the same steps. Run them yourself,
never through an agent.

```bash
NS=si; POD=<pod>

# Thread dump: trigger, find, copy
kubectl exec -n "$NS" "$POD" -- sh -c 'jcmd $(pgrep -f java | head -n 1) Dump.java'
JC=$(kubectl exec -n "$NS" "$POD" -- sh -c 'ls -t /opt/ibm/wlp/output/defaultServer/javacore.*.txt /tmp/javacore.*.txt 2>/dev/null | head -n 1')
kubectl exec -n "$NS" "$POD" -- cat "$JC" > runs/javacores/"$POD"-manual.txt

# GC log file (Liberty -Xloggc:logs/gc.log)
mkdir -p runs/gclogs/"$POD"-manual
kubectl exec -n "$NS" "$POD" -- cat /opt/ibm/wlp/output/defaultServer/logs/gc.log > runs/gclogs/"$POD"-manual/gc.log
```

`kubectl exec … cat` is used instead of `kubectl cp` because `cp` needs `tar`
in the image. `pgrep -f java` can also match the `sh -c` wrapper itself. It
works because the JVM is usually PID 1; the scripts look up the JVM by
`/proc/*/comm` instead.

## Troubleshooting

| Message | Meaning |
|---|---|
| `refusing to run: … interactive terminal` | Run it in your own terminal, not through Claude Code / Bob |
| `command terminated with exit code 5` followed by "writes its GC log to stderr" | Expected: the JVM logs GC to stderr. Use `jvm_get_gc_log_events` |
| `No verbose GC is configured for this JVM` | Add `-Xverbosegclog:<file>` (or `-verbose:gc`) to `jvm.options` and restart |
| `javacore was not found within 30s` | It prints the JVM PID, working directory, `jcmd` output, every directory it checked, and any existing javacores. Paste that to the agent |
| `WARNING: copied only X of Y bytes` | Transfer cut short; run again |
| `Error from server (Forbidden)` | Your identity can't `exec` in that namespace; ask for access |
| Agent says it "can't run the capture script" | By design. It hands you the command to run |
