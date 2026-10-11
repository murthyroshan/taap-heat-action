#!/usr/bin/env bash
# Build and deploy to AWS (Mumbai by default). First run asks for parameters and saves samconfig.toml.
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-ap-south-1}"
python3 -m pytest -q tests
sam validate --lint
sam build
if [ -f samconfig.toml ]; then
  sam deploy --region "$REGION"
else
  sam deploy --guided --stack-name taap --region "$REGION" --capabilities CAPABILITY_IAM
fi
aws cloudformation describe-stacks --stack-name taap --region "$REGION" --query "Stacks[0].Outputs" --output table
