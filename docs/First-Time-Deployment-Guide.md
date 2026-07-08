# 🚀 First-Time Deployment Guide

This walkthrough shows you how to set up a brand new Google Cloud Project from scratch and launch your real-time document search connectors.

---

## 📋 Phase 1: Prerequisites

Make sure you have these tools installed on your machine:
* **Google Cloud SDK (`gcloud`)**: For managing Google Cloud resources.
* **Terraform (`>= 1.0.0`)**: For setting up cloud infrastructure automatically.
* **Docker**: For building container images.
* **Python (`>= 3.11`)**: With `uv` or `pip` for running setup scripts.
* **Git**: For version control.

---

## ☁️ Phase 2: Google Cloud Project Setup

### 1. Set Your Environment Variables
Pick a unique compute project ID and region:
```bash
export PROJECT_ID="my-clean-deployment-proj-01"
export REGION="us-central1"
export REPO_NAME="gep-custom-connectors"

gcloud config set project $PROJECT_ID
```
> [!TIP]
> **Cross-Project Ingestion Feature:** In enterprise setups, compute resources (Cloud Run jobs, Cloud Scheduler, Secrets) reside in a dedicated compute project (`$PROJECT_ID`), while your Vertex AI Search / Discovery Engine datastores live in a central Search Hub project. Our Terraform automation natively supports this! When deploying cross-project, keep `$PROJECT_ID` set to your compute project here.

### 2. Enable Required APIs
Enable the Google Cloud services our app needs:
```bash
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  discoveryengine.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com
```

### 3. Authenticate Your CLI
Log in so your local tools can manage cloud resources:
```bash
gcloud auth login
gcloud auth application-default login
```

---

## 📦 Phase 3: Storage & Artifact Registry

### 1. Create a Container Registry
Create the place where your Docker container images will be stored:
```bash
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --description="Unified Custom Connectors Container Registry"
```

### 2. Create a Terraform State Bucket
Create a cloud storage bucket so Terraform can keep track of your setup:
```bash
export TF_STATE_BUCKET="${PROJECT_ID}-tf-state"
gcloud storage buckets create gs://$TF_STATE_BUCKET --location=$REGION --uniform-bucket-level-access
```

### 3. Create a Document Staging Bucket
Create a bucket to hold temporary document batches while syncing:
```bash
export STAGING_BUCKET="${PROJECT_ID}-connector-sources"
gcloud storage buckets create gs://$STAGING_BUCKET --location=$REGION --uniform-bucket-level-access
```

### 4. Create a Shared Cache Bucket
Create a single shared storage bucket for persistent cache databases across all pipeline jobs:
```bash
export CACHE_BUCKET="${PROJECT_ID}-connector-cache"
gcloud storage buckets create gs://$CACHE_BUCKET --location=$REGION --uniform-bucket-level-access
```
> [!NOTE]
> When explicitly specified in pipeline YAML configs (`cache_bucket_name`), Terraform assumes this shared bucket already exists. At runtime, the ingestion runner automatically isolates each job's SQLite database file by its configuration filename (e.g., `github_discussions_sync_cache.db`), preventing collisions.

---

## 🔐 Phase 4: Secret Manager

If your app needs API keys or private keys, store them safely in Secret Manager:
```bash
# Create secret container
gcloud secrets create ghes-app-private-key --replication-policy="automatic"

# Upload private key file
gcloud secrets versions add ghes-app-private-key --data-file=/path/to/app-private-key.pem
```
> [!IMPORTANT]
> Note down the full resource ID of your secret version (like `projects/123456789/secrets/ghes-app-private-key/versions/latest`). You will put this ID in your pipeline config.

---

## 🔍 Phase 5: Discovery Engine Setup

Before uploading documents, set up your search datastores and shared permission store:

1. Open [setup.ipynb](../setup.ipynb) in your IDE or Jupyter notebook.
2. Update the configuration block:
   ```python
   # Set to your central Discovery Engine Search Hub project (can be separate from compute project)
   PROJECT_ID = "my-search-hub-proj-01"
   LOCATION = "global"
   IMS_ID = "corporate-identity-store"

   # 1. Create shared Identity Mapping Store
   ims = get_or_create_identity_mapping_store(PROJECT_ID, LOCATION, IMS_ID)

   # 2. Create multiple Datastores sharing the single IMS
   ds_discussions = get_or_create_acl_enabled_data_store(PROJECT_ID, LOCATION, "discussions-ds", "Discussions", ims.name)
   ds_docs = get_or_create_acl_enabled_data_store(PROJECT_ID, LOCATION, "docs-ds", "Documentation", ims.name)
   ```
3. Run the notebook (or run via terminal):
   ```bash
   uv run jupyter nbconvert --to notebook --execute setup.ipynb
   ```
> [!NOTE]
> Linking multiple Datastores to a single Identity Mapping Store ensures enterprise user permissions remain synchronized across different document content types.

---

## ⚙️ Phase 6: Configure Your Pipeline

Create or edit your pipeline YAML file inside the `pipelines/` folder (like [pipelines/live_github_discussions_pipeline.yaml](../pipelines/live_github_discussions_pipeline.yaml)):

```yaml
pipeline:
  name: "GitHub Enterprise Discussions Sync"
  type: "document"
  fetcher:
    class: "src.github.fetchers.GitHubDiscussionsFetcher"
    params:
      app_id: "12345" # MODIFICATION REQUIRED: GitHub App ID
      installation_id: "67890" # MODIFICATION REQUIRED: App Installation ID
      repos:
        - "^MyOrg/.*" # MODIFICATION REQUIRED: Regex matching target enterprise repos
      base_url: "https://github.mycompany.com/api/v3" # MODIFICATION REQUIRED: GHES API base URL
      private_key_secret_name: "projects/my-clean-deployment-proj-01/secrets/ghes-app-private-key/versions/latest" # MODIFICATION REQUIRED: Secret Manager ID
  transformers:
    - class: "src.github.transformers.GitHubDiscussionTransformer"
      params:
        app_id: "12345" # MODIFICATION REQUIRED: GitHub App ID
        installation_id: "67890" # MODIFICATION REQUIRED: App Installation ID
        base_url: "https://github.mycompany.com/api/v3" # MODIFICATION REQUIRED: GHES API base URL
        private_key_secret_name: "projects/my-clean-deployment-proj-01/secrets/ghes-app-private-key/versions/latest" # MODIFICATION REQUIRED: Secret Manager ID
        enterprise_slug: "MyOrg" # MODIFICATION REQUIRED: GHES Org or Enterprise slug
        identity_mapper:
          class: "src.github.identity.GitHubCommitMiningIdentityMapper"
  uploader:
    class: "src.core.gcp_uploaders.GoogleCloudDiscoveryEngineDocumentUploader"
    params:
      # Target Discovery Engine project (deliberately enables cross-project ingestion if different from active compute project)
      project_id: "my-search-hub-proj-01" # MODIFICATION REQUIRED: Search Hub GCP Project ID
      data_store_id: "discussions-ds" # MODIFICATION REQUIRED: Target Discovery Engine Datastore ID
      location: "global"
      branch_id: "default_branch"
      gcs_bucket: "my-clean-deployment-proj-01-connector-sources" # MODIFICATION REQUIRED: GCS Staging Bucket

deployment:
  job_name: "github-discussions-sync"
  cron_schedule: "*/15 * * * *"
  reconciliation_mode: "INCREMENTAL"
  cache_bucket_name: "my-clean-deployment-proj-01-connector-cache" # MODIFICATION REQUIRED: Shared Persistent GCS Cache Bucket
  secret_accessor_ids:
    - "projects/my-clean-deployment-proj-01/secrets/ghes-app-private-key/versions/latest" # MODIFICATION REQUIRED: Secret Manager ID
  env_vars:
    GITHUB_PRIVATE_KEY_SECRET: "projects/my-clean-deployment-proj-01/secrets/ghes-app-private-key/versions/latest" # MODIFICATION REQUIRED: Secret Manager ID
    GITHUB_APP_ID: "12345" # MODIFICATION REQUIRED: GitHub App ID
    GITHUB_INSTALLATION_ID: "67890" # MODIFICATION REQUIRED: App Installation ID
```

### 🔑 Obtaining GitHub Enterprise App Credentials
If connecting to GitHub Enterprise Server (GHES) or GitHub Enterprise Cloud, you must register an internal GitHub App under your organization:

1. **App ID**: Go to **Organization Settings** $\rightarrow$ **Developer Settings** $\rightarrow$ **GitHub Apps** $\rightarrow$ select your app. The **App ID** is displayed at the top of the General settings tab.
2. **Private Key Certificate (.pem)**: On the same General settings tab, scroll down to **Private keys** and click **Generate a private key**. This downloads the `.pem` certificate file to your machine (which you upload to GCP in Phase 4).
3. **Installation ID**: Click **Install App** in the left sidebar and install it on your target organization. After installing, inspect your browser's address bar: `https://github.mycompany.com/organizations/MyOrg/settings/installations/67890`. The trailing number (`67890`) is your **Installation ID**.

---

## 🛠️ Phase 7: Deploy Your Job (`deploy.sh`)

Create a `.env` file at the root of your project:
```env
PROJECT_ID=my-clean-deployment-proj-01
REGION=us-central1
REPO_NAME=gep-custom-connectors
TF_STATE_BUCKET=my-clean-deployment-proj-01-tf-state
```

Run the deployment script:
```bash
./deploy.sh
```

### What `deploy.sh` Does:
1. **Checks Configs**: Verifies your YAML files have no syntax errors.
2. **Builds Container**: Builds your Docker container image and uploads it to Artifact Registry.
3. **Sets Up Cloud Jobs**: Runs Terraform to automatically plan and prompt for confirmation before creating Cloud Run jobs, schedules, and cross-project IAM service account bindings.

---

## ✅ Phase 8: Verify and Troubleshoot

### 1. Test Your Job
Run your Cloud Run job manually:
```bash
gcloud run jobs execute github-discussions-sync --region=$REGION
```

### 2. Check Logs
View your app logs in terminal:
```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=github-discussions-sync" \
  --limit=30 \
  --format="table(timestamp,textPayload)"
```

### 3. Common Issues
* **`403 Permission Denied on IAM Binding`**: When deploying cross-project (compute runners separate from Search Hub datastores), your local CLI identity (or remote `cd-pipeline-runner`) must have `resourcemanager.projectIamAdmin` permissions granted on the target Search Hub GCP project.
* **`403 Permission Denied at Runtime`**: Make sure your Cloud Run job service account has the `Discovery Engine Editor` role attached on the datastore project.
* **`SecretVersion not found`**: Check that the secret ID in your YAML file matches the exact secret name you created in Phase 4.
* **Terraform State Error**: Check that `TF_STATE_BUCKET` in your `.env` file matches the bucket you created in Phase 3.
