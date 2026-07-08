#!/usr/bin/env bash
set -euo pipefail

# Universal Local Deployment Runner for GEP Custom Connectors

# Load .env file if present
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "${PROJECT_ID:-}")
REGION="${REGION:-us-central1}"
REPO_NAME="${REPO_NAME:-gep-custom-connectors}"
IMAGE_NAME="custom-connectors"
COMMIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")

IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:$COMMIT_SHA"

echo "============================================================"
echo "     GEMINI CUSTOM CONNECTORS LOCAL DEPLOYER                "
echo "============================================================"
echo "  Active GCP Project : $PROJECT_ID"
echo "  Target GCP Region  : $REGION"
echo "  Artifact Registry  : $IMAGE_URI"
echo "============================================================"

# Step 1: Validate Pipeline YAML Syntax
echo "--- Validating Pipeline Definitions ---"
python3 -c "import yaml, glob; [yaml.safe_load(open(f)) for f in glob.glob('pipelines/*.yaml') + glob.glob('pipelines/*.yml')]"
echo "✓ All pipeline definition YAML files are valid."

# Step 2: Build & Push Container
echo "--- Building Docker Container ---"
docker build -t "$IMAGE_URI" -t "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest" .
echo "--- Pushing Docker Container ---"
docker push "$IMAGE_URI"
docker push "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest"

# Step 3: Provision Infrastructure via Terraform
echo "--- Provisioning Infrastructure via Terraform ---"
terraform -chdir=terraform/jobs init -reconfigure -backend-config="bucket=${TF_STATE_BUCKET:-reddit-gep-custom-connectors-terraform-state-23142425}" -backend-config="prefix=terraform/state/jobs"
terraform -chdir=terraform/jobs apply -var="project_id=$PROJECT_ID" -var="region=$REGION" -var="image_uri=$IMAGE_URI"

echo "============================================================"
echo "         ALL SELECTIONS DEPLOYED SUCCESSFULLY               "
echo "============================================================"
