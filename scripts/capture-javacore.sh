#!/usr/bin/env bash
# Capture OpenJ9 javacores (thread dumps) from one pod into runs/javacores/.
#
# HUMAN-RUN ONLY. This is the one place in this project that uses
# `kubectl exec`, which is why agents never run it: it refuses to start without
# an interactive terminal, asks you to type the pod name to confirm, and
# .claude/hooks/block_unsafe_shell.py denies it from the agent's Bash tool.
# The agent then analyzes the saved files with the read-only
# `jvm_analyze_javacore` MCP tool (or open them in IBM TMDA).
#
# What it does in the pod, per dump:
#   1. finds the java process by /proc/<pid>/comm (not `pgrep -f java`, which
#      can also match the `sh -c` wrapper whose command line mentions java),
#   2. asks the JVM for a javacore with `jcmd <pid> Dump.java`, falling back to
#      SIGQUIT (kill -3) if jcmd isn't in the image. Either way OpenJ9 keeps
#      running; application threads pause briefly while the dump is written,
#   3. streams the new javacore back with `cat` (kubectl cp would need tar).
# The javacore files are left in the pod (typically a few MB each).
#
# Take several dumps a few seconds apart (-n/-i) to tell threads that are
# stuck (same frame in every dump) from threads that are just busy.
#
# Usage: scripts/capture-javacore.sh [-n count] [-i interval_seconds] <namespace> <pod> [container]
#   e.g. bash scripts/capture-javacore.sh -n 3 -i 10 si time-series-query-d5fc57cf9-rnnkj

set -euo pipefail

COUNT=1
INTERVAL=10
while getopts "n:i:" opt; do
  case "$opt" in
    n) COUNT=$OPTARG ;;
    i) INTERVAL=$OPTARG ;;
    *) exit 64 ;;
  esac
done
shift $((OPTIND - 1))

if [ $# -lt 2 ]; then
  echo "usage: $0 [-n count] [-i interval_seconds] <namespace> <pod> [container]" >&2
  exit 64
fi
NAMESPACE=$1
POD=$2
CONTAINER=${3:-}

case "$COUNT" in ''|*[!0-9]*) echo "-n must be a positive integer" >&2; exit 64 ;; esac
case "$INTERVAL" in ''|*[!0-9]*) echo "-i must be a whole number of seconds" >&2; exit 64 ;; esac
if [ "$COUNT" -lt 1 ] || [ "$COUNT" -gt 10 ]; then
  echo "-n must be between 1 and 10" >&2
  exit 64
fi

if [ ! -t 0 ] || [ ! -t 1 ]; then
  echo "refusing to run: this script must be run by a human in an interactive terminal" >&2
  exit 2
fi

CONTEXT=$(kubectl config current-context)
echo "kubectl context : $CONTEXT"
echo "namespace       : $NAMESPACE"
echo "pod             : $POD${CONTAINER:+ (container $CONTAINER)}"
echo "dumps           : $COUNT$([ "$COUNT" -gt 1 ] && echo ", ${INTERVAL}s apart")"
echo
echo "This asks the JVM to write a javacore (jcmd Dump.java, or SIGQUIT). The JVM keeps running."
read -r -p "Type the pod name to confirm: " CONFIRM
if [ "$CONFIRM" != "$POD" ]; then
  echo "confirmation did not match; aborting" >&2
  exit 1
fi

EXEC_ARGS=(-n "$NAMESPACE" "$POD")
if [ -n "$CONTAINER" ]; then
  EXEC_ARGS+=(-c "$CONTAINER")
fi

# Runs inside the container using sh builtins plus cat (and jcmd if present).
# Prints the path of the new javacore on success.
REMOTE_SCRIPT='
set -e
pid=""
for d in /proc/[0-9]*; do
  c=""
  read -r c < "$d/comm" 2>/dev/null || continue
  if [ "$c" = "java" ]; then pid=${d#/proc/}; break; fi
done
[ -n "$pid" ] || { echo "no java process found in container" >&2; exit 3; }
cwd=$(cd "/proc/$pid/cwd" 2>/dev/null && pwd -P) || cwd=""
marker="/tmp/.javacore-marker-$$"
: > "$marker"
sleep 1  # make sure the new javacore is strictly newer than the marker
if command -v jcmd >/dev/null 2>&1 && jcmd "$pid" Dump.java >/dev/null 2>&1; then
  :
else
  kill -3 "$pid"
fi
i=0
while [ $i -lt 30 ]; do
  sleep 1
  i=$((i + 1))
  for dir in "${IBM_JAVACOREDIR:-}" "$cwd" /tmp /opt/ibm /opt/ibm/wlp/output/defaultServer; do
    [ -n "$dir" ] || continue
    for f in "$dir"/javacore*.txt; do
      if [ -f "$f" ] && [ "$f" -nt "$marker" ]; then
        sleep 2  # let the JVM finish writing
        rm -f "$marker"
        echo "$f"
        exit 0
      fi
    done
  done
done
rm -f "$marker"
echo "javacore was not found within 30s (checked IBM_JAVACOREDIR, the JVM cwd, /tmp, /opt/ibm, Liberty output dir)" >&2
exit 4
'

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT_DIR="$REPO_ROOT/runs/javacores"
mkdir -p "$OUT_DIR"
SAVED=()

for ((n = 1; n <= COUNT; n++)); do
  echo "Triggering javacore $n/$COUNT..."
  REMOTE_PATH=$(kubectl exec "${EXEC_ARGS[@]}" -- sh -c "$REMOTE_SCRIPT")
  OUT_FILE="$OUT_DIR/${POD}-$(date -u +%Y%m%dT%H%M%SZ)$([ "$COUNT" -gt 1 ] && echo "-$n").txt"
  kubectl exec "${EXEC_ARGS[@]}" -- cat "$REMOTE_PATH" > "$OUT_FILE"
  echo "  saved $(wc -c < "$OUT_FILE" | tr -d ' ') bytes from $REMOTE_PATH"
  SAVED+=("${OUT_FILE#"$REPO_ROOT"/}")
  if [ "$n" -lt "$COUNT" ]; then
    sleep "$INTERVAL"
  fi
done

echo
echo "Saved:"
printf '  %s\n' "${SAVED[@]}"
echo
echo "Ask the agent to analyze them, e.g.: run jvm_analyze_javacore on ${SAVED[0]}"
echo "Or open in IBM TMDA: java -Xmx2g -jar ~/tools/tmda/jca.jar ${SAVED[0]}"
