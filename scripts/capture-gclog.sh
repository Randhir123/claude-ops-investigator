#!/usr/bin/env bash
# Pull OpenJ9 verbose GC log files out of one pod into runs/gclogs/.
#
# HUMAN-RUN ONLY. Reading files inside a container needs `kubectl exec`, which
# agents are never allowed to do: this script refuses to start without an
# interactive terminal, asks you to type the pod name to confirm, and
# .claude/hooks/block_unsafe_shell.py denies it from the agent's Bash tool.
# The agent then analyzes the saved files with the read-only
# `jvm_analyze_gc_log` MCP tool (or open them in IBM GCMV).
#
# It only reads: nothing is signalled or changed in the pod. It works out
# where the JVM writes its GC log from the JVM itself:
#   - `-Xverbosegclog:<file>[,count,size]` on the command line or in
#     OPENJ9_JAVA_OPTIONS / IBM_JAVA_OPTIONS / JAVA_TOOL_OPTIONS /
#     JDK_JAVA_OPTIONS (%pid, %seq, %Y... tokens and rotated files included),
#   - bare `-Xverbosegclog` -> the default verbosegc.<date>.<time>.<pid>.txt
#     in the JVM's working directory,
#   - plus the usual spots: /tmp/verbosegc.*.txt, /opt/ibm/*verbosegc.*.txt.
# If the JVM only has `-verbose:gc` (GC log goes to stderr), there is no file:
# use the agent's `jvm_get_gc_log_events`, which reads it from `kubectl logs`.
# If neither is set there is no GC log at all; that needs a jvm.options change
# and a restart, which this script can't work around.
#
# Usage: scripts/capture-gclog.sh [-m max_files] <namespace> <pod> [container]
#   e.g. bash scripts/capture-gclog.sh si time-series-query-d5fc57cf9-rnnkj

set -euo pipefail

MAX_FILES=10
while getopts "m:" opt; do
  case "$opt" in
    m) MAX_FILES=$OPTARG ;;
    *) exit 64 ;;
  esac
done
shift $((OPTIND - 1))

if [ $# -lt 2 ]; then
  echo "usage: $0 [-m max_files] <namespace> <pod> [container]" >&2
  exit 64
fi
NAMESPACE=$1
POD=$2
CONTAINER=${3:-}

case "$MAX_FILES" in ''|*[!0-9]*) echo "-m must be a positive integer" >&2; exit 64 ;; esac
if [ "$MAX_FILES" -lt 1 ] || [ "$MAX_FILES" -gt 50 ]; then
  echo "-m must be between 1 and 50" >&2
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
echo "max files       : $MAX_FILES (newest first)"
echo
echo "This reads the JVM's verbose GC log file(s) out of the pod. Nothing in the pod is changed."
read -r -p "Type the pod name to confirm: " CONFIRM
if [ "$CONFIRM" != "$POD" ]; then
  echo "confirmation did not match; aborting" >&2
  exit 1
fi

EXEC_ARGS=(-n "$NAMESPACE" "$POD")
if [ -n "$CONTAINER" ]; then
  EXEC_ARGS+=(-c "$CONTAINER")
fi

# Runs inside the container (sh, tr, sed, ls, head, wc). Output protocol:
#   SPEC <what the JVM is configured with>
#   FILE <bytes> <path>       one line per GC log file, newest first
# Exit 5: GC log goes to stderr (-verbose:gc only). Exit 6: no verbose GC configured.
REMOTE_SCRIPT='
set -e
pid=""
for d in /proc/[0-9]*; do
  c=""
  read -r c < "$d/comm" 2>/dev/null || continue
  if [ "$c" = "java" ]; then pid=${d#/proc/}; break; fi
done
[ -n "$pid" ] || { echo "no java process found in container" >&2; exit 3; }
cwd=$(cd "/proc/$pid/cwd" 2>/dev/null && pwd -P) || cwd="."

opts="/tmp/.gclog-opts-$$"
tr "\0" "\n" < "/proc/$pid/cmdline" > "$opts"
tr "\0" "\n" < "/proc/$pid/environ" 2>/dev/null | while IFS= read -r kv; do
  case "$kv" in
    OPENJ9_JAVA_OPTIONS=*|IBM_JAVA_OPTIONS=*|JAVA_TOOL_OPTIONS=*|JDK_JAVA_OPTIONS=*)
      printf "%s\n" "${kv#*=}" | tr " " "\n" ;;
  esac
done >> "$opts"

spec=""; bare=""; stderr_gc=""
while IFS= read -r o; do
  case "$o" in
    -Xverbosegclog:*) spec=${o#-Xverbosegclog:} ;;
    -Xverbosegclog) bare=1 ;;
    -verbose:gc|-verbose:gc,*) stderr_gc=1 ;;
  esac
done < "$opts"
rm -f "$opts"

patterns=""
if [ -n "$spec" ]; then
  echo "SPEC -Xverbosegclog:$spec"
  f=${spec%%,*}
  case "$f" in /*) ;; *) f="$cwd/$f" ;; esac
  g=$(printf "%s" "$f" | sed "s/%[A-Za-z]*/*/g")
  patterns="$g $g.*"
elif [ -n "$bare" ]; then
  echo "SPEC -Xverbosegclog (default file name)"
  patterns="$cwd/verbosegc.*.txt"
elif [ -n "$stderr_gc" ]; then
  echo "SPEC -verbose:gc (stderr)"
  exit 5
else
  echo "SPEC none"
fi
patterns="$patterns /tmp/verbosegc.*.txt /opt/ibm/*verbosegc.*.txt $cwd/verbosegc.*.txt"

found=0
for f in $(ls -t $patterns 2>/dev/null | head -n "$MAX"); do
  [ -f "$f" ] || continue
  size=$(wc -c < "$f" 2>/dev/null || echo "?")
  echo "FILE $size $f"
  found=1
done
if [ "$found" = 0 ]; then
  if [ -z "$spec" ] && [ -z "$bare" ]; then exit 6; fi
  echo "verbose GC is configured but no log file was found (checked: $patterns)" >&2
  exit 4
fi
'

echo "Looking up the JVM's GC log configuration..."
RC=0
LISTING=$(kubectl exec "${EXEC_ARGS[@]}" -- sh -c "MAX=$MAX_FILES; $REMOTE_SCRIPT") || RC=$?
SPEC_LINE=$(printf '%s\n' "$LISTING" | sed -n 's/^SPEC //p' | head -n 1)
[ -n "$SPEC_LINE" ] && echo "  JVM is configured with: $SPEC_LINE"

if [ "$RC" -eq 5 ]; then
  echo
  echo "This JVM writes its GC log to stderr (-verbose:gc only), so there is no file to copy."
  echo "Ask the agent to run jvm_get_gc_log_events for $NAMESPACE/$POD instead; it reads kubectl logs."
  exit 0
fi
if [ "$RC" -eq 6 ]; then
  echo
  echo "No verbose GC is configured for this JVM, so there is no GC log to retrieve." >&2
  echo "It needs -Xverbosegclog:<file> (or -verbose:gc) in jvm.options and a restart." >&2
  exit 1
fi
if [ "$RC" -ne 0 ]; then
  exit "$RC"
fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT_DIR="$REPO_ROOT/runs/gclogs/${POD}-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT_DIR"

SEEN=$'\n'  # newline-delimited list (macOS ships bash 3.2: no associative arrays)
N=0
while read -r TAG SIZE REMOTE_PATH; do
  [ "$TAG" = "FILE" ] || continue
  case "$SEEN" in *$'\n'"$REMOTE_PATH"$'\n'*) continue ;; esac
  SEEN="$SEEN$REMOTE_PATH"$'\n'
  N=$((N + 1))
  NAME=$(basename "$REMOTE_PATH")
  [ -e "$OUT_DIR/$NAME" ] && NAME="$N-$NAME"
  echo "  copying $REMOTE_PATH ($SIZE bytes)"
  kubectl exec "${EXEC_ARGS[@]}" -- cat "$REMOTE_PATH" > "$OUT_DIR/$NAME"
done <<< "$LISTING"

REL_DIR=${OUT_DIR#"$REPO_ROOT"/}
echo
echo "Saved $N file(s) to $REL_DIR"
echo "Ask the agent to analyze them, e.g.: run jvm_analyze_gc_log on $REL_DIR"
echo "Or open them in IBM GCMV."
