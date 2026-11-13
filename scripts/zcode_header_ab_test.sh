#!/usr/bin/env bash
# Quota-consumption A/B experiment: ZCode attribution headers vs. plain requests
#
# Usage:
#   ZHIPU_KEY=your_key ./zcode_header_ab_test.sh [requests_per_group] [model]
#
# How it works:
#   Using the same GLM Coding Plan key, send the exact same request body twice:
#     Group A: plain curl headers (control group)
#     Group B: full ZCode attribution headers (experiment group)
#   Query /api/monitor/usage/quota/limit once before and after each group, then compare the consumed delta.
set -euo pipefail

KEY="${ZHIPU_KEY:?Please export ZHIPU_KEY=your_GLM_Coding_Plan_Key first}"
N="${1:-10}"            # requests per group
MODEL="${2:-glm-4.7}"   # model name supported by both endpoints (adjust to your plan)
BASE="${ZHIPU_BASE:-https://api.z.ai}"

ENDPOINT="$BASE/api/anthropic/v1/messages"
QUOTA_URL="$BASE/api/monitor/usage/quota/limit"

# Identical request body: fixed prompt + fixed max_tokens => output token count is nearly constant
BODY=$(cat <<EOF
{
  "model": "$MODEL",
  "max_tokens": 512,
  "temperature": 0,
  "messages": [{"role": "user", "content": "Count from 1 to 100, one number per line, and output nothing else."}]
}
EOF
)

query_quota() {
  curl -sS "$QUOTA_URL" -H "Authorization: $KEY"
}

send_plain() {
  # Group A: no ZCode-specific traits
  curl -sS "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -H "Authorization: $KEY" \
    -H "anthropic-version: 2023-06-01" \
    -d "$BODY" > /dev/null
}

send_zcode() {
  # Group B: replicate ZCode's full attribution headers
  curl -sS "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -H "Authorization: $KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "User-Agent: ZCode/3.0.0" \
    -H "X-ZCode-App-Version: 3.0.0" \
    -H "X-ZCode-Agent: glm" \
    -H "X-Title: Z Code@cli" \
    -H "HTTP-Referer: https://zcode.z.ai" \
    -H "X-Release-Channel: stable" \
    -H "X-Client-Language: zh-CN" \
    -H "X-Client-Timezone: Asia/Shanghai" \
    -H "X-Platform: darwin-arm64" \
    -H "X-Os-Category: macos" \
    -H "X-Device-Mid: 00000000-0000-0000-0000-000000000000" \
    -d "$BODY" > /dev/null
}

echo "===== Baseline quota ====="
query_quota | tee /tmp/quota_baseline.json
echo

echo "===== Group A: plain requests x $N ====="
for i in $(seq "$N"); do
  echo "-- A$i"
  send_plain
done

echo "===== Quota after group A ====="
query_quota | tee /tmp/quota_after_a.json
echo

echo "===== Group B: ZCode-header requests x $N ====="
for i in $(seq "$N"); do
  echo "-- B$i"
  send_zcode
done

echo "===== Quota after group B ====="
query_quota | tee /tmp/quota_after_b.json
echo

echo "Result files: /tmp/quota_baseline.json /tmp/quota_after_a.json /tmp/quota_after_b.json"
