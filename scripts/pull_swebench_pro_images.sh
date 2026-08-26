#!/bin/bash

IMAGE_LIST="/tmp/swebench_pro_python_images.txt"
LOG_FILE="/tmp/swebench_pro_pull.log"
FAIL_FILE="/tmp/swebench_pro_pull_failed.txt"
MAX_RETRIES=5
PULL_INTERVAL=5

if [ ! -f "$IMAGE_LIST" ]; then
    echo "ERROR: Image list not found: $IMAGE_LIST"
    exit 1
fi

TOTAL=$(wc -l < "$IMAGE_LIST")
echo "=========================================="
echo " SWE-bench Pro Image Pull"
echo " Total images: $TOTAL"
echo " Pull interval: ${PULL_INTERVAL}s"
echo " Log: $LOG_FILE"
echo "=========================================="
echo ""

> "$FAIL_FILE"
PULLED=0
SKIPPED=0
FAILED=0
IDX=0

while IFS= read -r IMAGE; do
    IDX=$((IDX + 1))

    if docker image inspect "$IMAGE" > /dev/null 2>&1; then
        SKIPPED=$((SKIPPED + 1))
        echo "[$IDX/$TOTAL] SKIP (exists): ...${IMAGE: -60}"
        continue
    fi

    SUCCESS=false
    for RETRY in $(seq 1 $MAX_RETRIES); do
        echo -n "[$IDX/$TOTAL] Pulling (try $RETRY): ...${IMAGE: -60} ... "

        OUTPUT=$(docker pull "$IMAGE" 2>&1)
        EXIT_CODE=$?
        echo "$OUTPUT" >> "$LOG_FILE"

        if [ $EXIT_CODE -eq 0 ]; then
            PULLED=$((PULLED + 1))
            echo "OK"
            SUCCESS=true
            break
        fi

        if echo "$OUTPUT" | grep -qi "toomanyrequests\|rate limit"; then
            WAIT=$((60 * RETRY))
            echo "RATE LIMITED — waiting ${WAIT}s before retry..."
            sleep $WAIT
        else
            echo "FAILED (non-rate-limit error)"
            echo "$OUTPUT" | tail -2
            break
        fi
    done

    if [ "$SUCCESS" = false ]; then
        FAILED=$((FAILED + 1))
        echo "$IMAGE" >> "$FAIL_FILE"
    fi

    sleep $PULL_INTERVAL

done < "$IMAGE_LIST"

echo ""
echo "=========================================="
echo " Done!"
echo " Pulled:  $PULLED"
echo " Skipped: $SKIPPED (already existed)"
echo " Failed:  $FAILED"
echo " Total:   $TOTAL"
echo "=========================================="

if [ "$FAILED" -gt 0 ]; then
    echo ""
    echo "Failed images saved to: $FAIL_FILE"
    cat "$FAIL_FILE"
fi
