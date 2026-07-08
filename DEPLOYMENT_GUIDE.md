# Deployment Guide: Gemini Enterprise Custom Connector

Follow these steps to deploy the modified Snooguts Mission Control connector into Google Cloud and Gemini Enterprise.

## Prerequisites
- Google Cloud Project with Billing enabled.
- `gcloud` SDK installed and authenticated.
- `terraform` (v1.0.0+) installed.
- Python 3.9+ for initial data preparation.

## Step 1: Project Environment Setup
Set your active project and enable the necessary APIs.

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"

gcloud config set project $PROJECT_ID
gcloud services enable \
    discoveryengine.googleapis.com \
    bigquery.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    cloudresourcemanager.googleapis.com \
    iam.googleapis.com
```

## Step 2: BigQuery Mock Data Setup
Prepare the dataset and load the mock data for testing.

```bash
# 1. Create the dataset
bq mk --dataset $PROJECT_ID:snooguts_mock

# 2. Split the mock data into JSONL
python3 prepare_bq_data.py

# 3. Load tables into BigQuery
for table in initiatives commitments launches person user; do
  bq load --autodetect --source_format=NEWLINE_DELIMITED_JSON \
    snooguts_mock.$table bq_load/$table.jsonl
done
```

## Step 3: Vertex AI Search Infrastructure
Create the Data Store and the staging GCS bucket.

```bash
# 1. Create Data Store (Generic Search)
TOKEN=$(gcloud auth print-access-token)
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Goog-User-Project: $PROJECT_ID" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/collections/default_collection/dataStores?dataStoreId=snooguts-ds" \
  -d '{
    "displayName": "Snooguts Mock Data Store",
    "industryVertical": "GENERIC",
    "contentConfig": "CONTENT_REQUIRED",
    "solutionTypes": ["SOLUTION_TYPE_SEARCH"]
  }'

# 2. Create Staging Bucket
gcloud storage buckets create gs://cc-mission-control --location=$REGION --uniform-bucket-level-access
```

## Step 4: Build the Container Image
We use Google Cloud Build to create the production image and push it to Artifact Registry.

```bash
# 1. Create Artifact Registry repository
gcloud artifacts repositories create gep-custom-connectors \
    --repository-format=docker --location=$REGION

# 2. Create a temporary bucket for Cloud Build source
gcloud storage buckets create gs://$PROJECT_ID-build-source

# 3. Submit build
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest \
    --gcs-source-staging-dir gs://$PROJECT_ID-build-source/source .
```

## Step 5: Provision Cloud Run Job (Terraform)
Initialize and apply the Terraform configuration to create the sync job and schedule.

```bash
export IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest"

# Note: The backend in terraform/jobs/main.tf is set to 'local' for this repo.
terraform -chdir=terraform/jobs init -reconfigure
terraform -chdir=terraform/jobs apply -auto-approve \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="image_uri=$IMAGE_URI"
```

## Step 6: Trigger & Verify
Trigger the sync manually to verify everything is working.

```bash
# Execute the job
gcloud run jobs execute snooguts-mock-test-sync --region $REGION --wait

# View logs if needed
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=snooguts-mock-test-sync" --limit=50
```

Documents will now appear in your **Vertex AI Search Console** under the `snooguts-ds` data store.
