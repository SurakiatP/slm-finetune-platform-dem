#!/usr/bin/env bash
# Curl-only quickstart for the SLM Fine-Tuning Platform (Phase 9 flow).
# Walks: project → upload-seed → SDG → training → export → inference.
#
# Phase 9 changes vs Phase 4:
#   • SDG with_seed mode references the seed by id (uploaded first via
#     /datasets/upload-seed) instead of inlining seed_data.
#   • teacher_model is no longer accepted as a request override —
#     model strings are hardcoded server-side.
#   • upload-seed returns a `format_detection` audit (ran/field_mapping/
#     rows_dropped) and an optional `pdf_uri` for QA + PDF uploads.
#
# Usage:
#   chmod +x examples/quickstart_curl.sh
#   ./examples/quickstart_curl.sh
#
# Environment:
#   API=http://localhost:8000   (override the API base URL)
#
# Requires: bash, curl, jq.

set -euo pipefail

API="${API:-http://localhost:8000}"

# 1. Health check
echo "== Health =="
curl -sf "${API}/health" | jq

# 2. Create a project
echo "== Create project =="
PROJECT_ID=$(
  curl -sf -X POST "${API}/api/v1/projects" \
    -H 'Content-Type: application/json' \
    -d '{
      "name": "curl-demo",
      "description": "QA demo via curl",
      "task_type": "qa"
    }' | jq -r '.id'
)
echo "  project_id=${PROJECT_ID}"

# 3. List supported task types and base models
echo "== Supported task types =="
curl -sf "${API}/api/v1/tasks" | jq '.[].task_type'

echo "== Supported base models =="
curl -sf "${API}/api/v1/base-models" | jq '.[].id'

# 4. Upload a seed dataset (JSONL). Phase 9 — Format Detection runs here.
echo "== Upload seed =="
SEED_FILE=$(mktemp -t seed-XXXXXX).jsonl
cat > "${SEED_FILE}" <<'EOF'
{"question": "What's the return window?", "answer": "30 days."}
{"question": "Do I need a receipt?", "answer": "Yes, please keep it."}
{"question": "Sale items returnable?", "answer": "Sale items are final."}
{"question": "Refund timing?", "answer": "5-7 business days."}
{"question": "Where to ship?", "answer": "Returns Lane 123."}
EOF
SEED_RESPONSE=$(
  curl -sf -X POST "${API}/api/v1/datasets/upload-seed" \
    -F "project_id=${PROJECT_ID}" \
    -F "task_type=qa" \
    -F "name=seed-v1" \
    -F "file=@${SEED_FILE};type=application/x-ndjson"
)
SEED_DATASET_ID=$(echo "${SEED_RESPONSE}" | jq -r '.dataset_id')
echo "  seed_dataset_id=${SEED_DATASET_ID}"
echo "  format_detection: $(echo "${SEED_RESPONSE}" | jq -c '.format_detection')"

# 5. Submit SDG (with_seed → seed_dataset_id)
echo "== Submit SDG =="
SDG_RESPONSE=$(
  curl -sf -X POST "${API}/api/v1/datasets/generate" \
    -H 'Content-Type: application/json' \
    -d "{
      \"sdg_mode\": \"with_seed\",
      \"project_id\": \"${PROJECT_ID}\",
      \"task_type\": \"qa\",
      \"task_description\": \"Answer questions about our return policy\",
      \"num_samples\": 20,
      \"seed_dataset_id\": \"${SEED_DATASET_ID}\"
    }"
)
DATASET_ID=$(echo "${SDG_RESPONSE}" | jq -r '.dataset_id')
SDG_JOB_ID=$(echo "${SDG_RESPONSE}" | jq -r '.job_id')
echo "  dataset_id=${DATASET_ID} job_id=${SDG_JOB_ID}"
echo "  WebSocket: ws://${API#http://}/ws/jobs/${SDG_JOB_ID}"

# 6. Poll dataset until storage_uri is set (means SDG completed)
echo "== Wait for SDG completion =="
for i in $(seq 1 60); do
  STATUS=$(curl -sf "${API}/api/v1/datasets/${DATASET_ID}" | jq -r '.storage_uri // ""')
  if [ -n "${STATUS}" ] && [ "${STATUS}" != "null" ]; then
    echo "  done after ${i} polls; storage_uri=${STATUS}"
    break
  fi
  sleep 5
done

# 7. Preview the dataset
echo "== Preview =="
curl -sf "${API}/api/v1/datasets/${DATASET_ID}/preview?limit=3" | jq

# 8. Submit a manual training job (requires GPU worker)
echo "== Submit training (requires GPU) =="
curl -sf -X POST "${API}/api/v1/trainings" \
  -H 'Content-Type: application/json' \
  -d "{
    \"mode\": \"manual\",
    \"project_id\": \"${PROJECT_ID}\",
    \"dataset_id\": \"${DATASET_ID}\",
    \"manual_config\": {
      \"learning_rate\": 2e-4,
      \"num_train_epochs\": 1,
      \"per_device_train_batch_size\": 1
    }
  }" | jq

echo "== Done =="
echo "Next steps:"
echo "  - watch progress:  websocat ws://${API#http://}/ws/jobs/<job-id>"
echo "  - export to GGUF:  curl -X POST ${API}/api/v1/models/<model-id>/export -d '{\"format\":\"gguf\"}'"
echo "  - chat:            curl ${API}/api/v1/inference/chat/completions -d '{\"model\":\"<id>\",\"messages\":[...]}'"
