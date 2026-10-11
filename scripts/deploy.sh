#!/usr/bin/env bash
# Deploy to AWS (Mumbai by default). No `sam build` needed: the functions have no
# third-party dependencies, so SAM uploads src/ as-is (works with any local Python).
# First run asks for parameters and saves samconfig.toml.
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-ap-south-1}"
if python -c "import pytest" 2>/dev/null; then python -m pytest -q tests; fi
if [ -f samconfig.toml ]; then
  sam deploy --region "$REGION"
else
  sam deploy --guided --stack-name taap --region "$REGION" --capabilities CAPABILITY_IAM
fi
aws cloudformation describe-stacks --stack-name taap --region "$REGION" --query "Stacks[0].Outputs" --output table
