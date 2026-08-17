#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 CONFIG OUTPUT_ROOT DATASET_ID" >&2
  exit 2
fi

CONFIG=$1
OUTPUT_ROOT=$2
DATASET_ID=$3
TOOL_BIN=${TOOL_BIN:-memory-benchtool}
PYTHON_BIN=${PYTHON_BIN:-python3}
STRUCTURES=${STRUCTURES:-"list hash set"}
ROW_TIERS=${ROW_TIERS:-"1000000 5000000 10000000"}
CLIENT_TIERS=${CLIENT_TIERS:-"128 256 512"}
MODES=${MODES:-"read write mixed"}
ROUNDS=${ROUNDS:-3}
ROWS_PER_KEY=${ROWS_PER_KEY:-1000}
VALUE_SIZE=${VALUE_SIZE:-128}
SEED_CLIENTS=${SEED_CLIENTS:-32}
DURATION_SECONDS=${DURATION_SECONDS:-10}
WARMUP_SECONDS=${WARMUP_SECONDS:-2}
MIN_FREE_DISK_GIB=${MIN_FREE_DISK_GIB:-8}
MIN_AVAILABLE_MEMORY_GIB=${MIN_AVAILABLE_MEMORY_GIB:-4}
KEEP_DATASETS=${KEEP_DATASETS:-0}

result_completed() {
  local path=$1
  [[ -f "$path" ]] || return 1
  "$PYTHON_BIN" - "$path" <<'PY'
import json
import sys

records = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if records and all(
    item.get("status") == "completed" for item in records
) else 1)
PY
}

mkdir -p "$OUTPUT_ROOT"
cat > "$OUTPUT_ROOT/matrix-parameters.txt" <<EOF
dataset_id=$DATASET_ID
structures=$STRUCTURES
row_tiers=$ROW_TIERS
client_tiers=$CLIENT_TIERS
modes=$MODES
rounds=$ROUNDS
rows_per_key=$ROWS_PER_KEY
value_size=$VALUE_SIZE
seed_clients=$SEED_CLIENTS
duration_seconds=$DURATION_SECONDS
warmup_seconds=$WARMUP_SECONDS
EOF

for structure in $STRUCTURES; do
  largest_rows=0
  for rows in $ROW_TIERS; do
    largest_rows=$rows
    seed_dir="$OUTPUT_ROOT/$structure/$rows/seed"
    "$TOOL_BIN" seed \
      --config "$CONFIG" --output-dir "$seed_dir" \
      --structure "$structure" --rows "$rows" \
      --rows-per-key "$ROWS_PER_KEY" --value-size "$VALUE_SIZE" \
      --dataset-id "$DATASET_ID" --clients "$SEED_CLIENTS" \
      --min-free-disk-gib "$MIN_FREE_DISK_GIB" \
      --min-available-memory-gib "$MIN_AVAILABLE_MEMORY_GIB"

    for clients in $CLIENT_TIERS; do
      for mode in $MODES; do
        for round in $(seq 1 "$ROUNDS"); do
          result_dir="$OUTPUT_ROOT/$structure/$rows/$clients/$mode/round-$round"
          if result_completed "$result_dir/workload.json"; then
            continue
          fi
          "$TOOL_BIN" workload \
            --config "$CONFIG" --output-dir "$result_dir" \
            --structure "$structure" --rows "$rows" \
            --rows-per-key "$ROWS_PER_KEY" --value-size "$VALUE_SIZE" \
            --dataset-id "$DATASET_ID" --mode "$mode" --clients "$clients" \
            --duration-seconds "$DURATION_SECONDS" \
            --warmup-seconds "$WARMUP_SECONDS" --read-ratio 50
        done
      done
    done
  done

  if [[ "$KEEP_DATASETS" != "1" ]]; then
    "$TOOL_BIN" cleanup-dataset \
      --config "$CONFIG" --output-dir "$OUTPUT_ROOT/$structure/cleanup" \
      --structure "$structure" --rows "$largest_rows" \
      --rows-per-key "$ROWS_PER_KEY" --value-size "$VALUE_SIZE" \
      --dataset-id "$DATASET_ID"
  fi
done
