#!/usr/bin/env bash
# Remove javacores (thread dumps) from a pod once you have copied and analyzed them.
#
# HUMAN-RUN ONLY. Deleting files inside a container needs `kubectl exec`, which
# agents are never allowed to do: this script refuses to start without an
# interactive terminal, and .claude/hooks/block_unsafe_shell.py denies it from
# the agent's Bash tool.
#
# What it removes:
#   default  the javacores scripts/capture-javacore.sh took from this pod, as
#            recorded in runs/javacores/.in-pod/<namespace>_<pod>.txt
#   -a       every javacore*.txt in the JVM's dump folders (IBM_JAVACOREDIR,
#            -Xdump directory/file, its working directory and logs/, /tmp,
#            /opt/ibm, Liberty output dirs): use it for dumps taken before
#            that record existed, or by hand. This also includes javacores the
#            JVM wrote on its own (e.g. on an OutOfMemoryError), so check the
#            list it shows.
# It lists the files with their sizes and asks once before deleting (-y skips
# the question). Only files named javacore*.txt are ever deleted.
#
# Usage: scripts/cleanup-javacores.sh [-a] [-y] <namespace> <pod> [container]

set -euo pipefail

MODE=recorded
YES=""
while getopts "ay" opt; do
  case "$opt" in
    a) MODE=all ;;
    y) YES=1 ;;
    *) exit 64 ;;
  esac
done
shift $((OPTIND - 1))

if [ $# -lt 2 ]; then
  echo "usage: $0 [-a] [-y] <namespace> <pod> [container]" >&2
  exit 64
fi
NAMESPACE=$1
POD=$2
CONTAINER=${3:-}

if [ ! -t 0 ] || [ ! -t 1 ]; then
  echo "refusing to run: this script must be run by a human in an interactive terminal" >&2
  exit 2
fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
IN_POD_LIST="$REPO_ROOT/runs/javacores/.in-pod/${NAMESPACE}_${POD}.txt"

CONTEXT=$(kubectl config current-context)
echo "kubectl context : $CONTEXT"
echo "namespace       : $NAMESPACE"
echo "pod             : $POD${CONTAINER:+ (container $CONTAINER)}"
if [ "$MODE" = all ]; then
  echo "removing        : every javacore*.txt in the JVM dump folders (-a)"
else
  echo "removing        : javacores taken with capture-javacore.sh (${IN_POD_LIST#"$REPO_ROOT"/})"
fi
echo

EXEC_ARGS=(-n "$NAMESPACE" "$POD")
if [ -n "$CONTAINER" ]; then
  EXEC_ARGS+=(-c "$CONTAINER")
fi

RECORDED=()
if [ "$MODE" = recorded ]; then
  if [ -s "$IN_POD_LIST" ]; then
    while IFS= read -r p; do
      if [ -n "$p" ]; then RECORDED+=("$p"); fi
    done < "$IN_POD_LIST"
  fi
  if [ "${#RECORDED[@]}" -eq 0 ]; then
    echo "No javacores recorded for this pod. To find all javacores in its dump folders, run:"
    echo "  scripts/cleanup-javacores.sh -a $NAMESPACE $POD${CONTAINER:+ $CONTAINER}"
    exit 0
  fi
fi

# Runs inside the container. Prints "FILE <bytes> <path>" for each javacore that
# exists: the paths passed as arguments (recorded mode), or every javacore*.txt
# in the JVM dump folders (all mode).
LIST_SCRIPT='
set -e
if [ "$MODE" = all ]; then
  pid=""
  for d in /proc/[0-9]*; do
    c=""
    read -r c < "$d/comm" 2>/dev/null || continue
    if [ "$c" = "java" ]; then pid=${d#/proc/}; break; fi
  done
  [ -n "$pid" ] || { echo "no java process found in container" >&2; exit 3; }
  cwd=$(cd "/proc/$pid/cwd" 2>/dev/null && pwd -P) || cwd="."
  abs() { case "$1" in /*) echo "$1" ;; *) echo "$cwd/$1" ;; esac; }
  dirs=""
  jdir=$(tr "\0" "\n" < "/proc/$pid/environ" 2>/dev/null | sed -n "s/^IBM_JAVACOREDIR=//p" | head -n 1)
  if [ -n "$jdir" ]; then dirs="$dirs $(abs "$jdir")"; fi
  for o in $(tr "\0" "\n" < "/proc/$pid/cmdline" 2>/dev/null | sed -n "/^-Xdump/p"); do
    d=$(printf "%s" "$o" | sed -n "s/.*directory=\([^,]*\).*/\1/p")
    if [ -n "$d" ]; then dirs="$dirs $(abs "$d")"; fi
    f=$(printf "%s" "$o" | sed -n "s/.*file=\([^,]*\).*/\1/p")
    if [ -n "$f" ]; then dirs="$dirs $(abs "${f%/*}")"; fi
  done
  dirs="$dirs $cwd $cwd/logs /logs /tmp /opt/ibm /opt/ibm/wlp/output/defaultServer /opt/ibm/wlp/output/defaultServer/logs"
  for dir in $dirs; do
    for f in "$dir"/javacore*.txt; do
      if [ -f "$f" ]; then echo "FILE $(wc -c < "$f") $f"; fi
    done
  done
else
  for f in "$@"; do
    if [ -f "$f" ]; then echo "FILE $(wc -c < "$f") $f"; fi
  done
fi
true
'

list_in_pod() {  # $1 = mode, rest = paths
  local mode=$1
  shift
  kubectl exec "${EXEC_ARGS[@]}" -- sh -c "MODE=$mode; $LIST_SCRIPT" sh "$@"
}

echo "Looking for javacores in the pod..."
if [ "$MODE" = all ]; then
  LISTING=$(list_in_pod all)
else
  LISTING=$(list_in_pod recorded "${RECORDED[@]}")
fi

FILES=()
TOTAL=0
SEEN=$'\n'  # newline-delimited list (macOS ships bash 3.2: no associative arrays)
while read -r TAG SIZE REMOTE_PATH; do
  [ "$TAG" = "FILE" ] || continue
  case "$SEEN" in *$'\n'"$REMOTE_PATH"$'\n'*) continue ;; esac
  SEEN="$SEEN$REMOTE_PATH"$'\n'
  case "$(basename "$REMOTE_PATH")" in
    javacore*.txt) ;;
    *) echo "  skipping $REMOTE_PATH (not named javacore*.txt)" >&2; continue ;;
  esac
  FILES+=("$REMOTE_PATH")
  TOTAL=$((TOTAL + SIZE))
  printf '  %10s bytes  %s\n' "$SIZE" "$REMOTE_PATH"
done <<< "$LISTING"

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "Nothing to remove: none of those javacores are in the pod any more."
  rm -f "$IN_POD_LIST"
  exit 0
fi

echo
if [ -z "$YES" ]; then
  read -r -p "Delete these ${#FILES[@]} file(s) ($((TOTAL / 1024 / 1024)) MB) from the pod? [y/N] " ANSWER
  case "$ANSWER" in
    y|Y|yes|YES) ;;
    *) echo "Nothing deleted."; exit 0 ;;
  esac
fi

kubectl exec "${EXEC_ARGS[@]}" -- rm -f -- "${FILES[@]}"

REMAINING=$(list_in_pod recorded "${FILES[@]}" | grep -c '^FILE ' || true)
if [ "$REMAINING" -ne 0 ]; then
  echo "WARNING: $REMAINING file(s) could not be removed (permissions?)." >&2
  exit 1
fi
rm -f "$IN_POD_LIST"
echo "Removed ${#FILES[@]} file(s), $((TOTAL / 1024 / 1024)) MB freed in $POD."
