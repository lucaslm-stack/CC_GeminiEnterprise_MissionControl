# 🔄 Enabling Continuous Deployment (CI/CD)

Ready to put your deployments on autopilot? This guide walks you through setting up **Google Cloud Build** so that whenever your team merges a reviewed Pull Request into the `main` branch, Cloud Build automatically checks your YAML configs, builds your Docker images, and updates your infrastructure with Terraform!

---

## 📋 Phase 1: Prerequisites & API Setup

First, let's make sure your terminal is logged into your active compute project:
```bash
export PROJECT_ID="my-clean-deployment-proj-01"
export REGION="us-central1"
export REPO_NAME="gep-custom-connectors"

gcloud config set project $PROJECT_ID
```

Next, enable the Cloud Build service:
```bash
gcloud services enable cloudbuild.googleapis.com
```

---

## 🔐 Phase 2: Dedicated Runner Service Account

When Cloud Build runs remote jobs, it spins up clean containers behind the scenes. To keep things secure, we give Cloud Build its own dedicated identity (`cd-pipeline-runner`) with just the permissions it needs.

### 1. Create the Service Account
```bash
gcloud iam service-accounts create cd-pipeline-runner \
  --display-name="Continuous Deployment Pipeline Runner" \
  --description="Service account used by Cloud Build to run container builds and Terraform updates."
```

### 2. Grant Least-Privilege IAM Roles
Let's give our runner the exact permissions required to build images, schedule jobs, manage storage buckets, and bind security policies:

```bash
export SA_EMAIL="cd-pipeline-runner@${PROJECT_ID}.iam.gserviceaccount.com"

for role in \
  roles/artifactregistry.writer \
  roles/run.admin \
  roles/cloudscheduler.admin \
  roles/logging.logWriter \
  roles/resourcemanager.projectIamAdmin \
  roles/secretmanager.admin \
  roles/iam.serviceAccountAdmin \
  roles/iam.serviceAccountUser \
  roles/storage.admin \
  roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role"
done
```
> [!IMPORTANT]
> This 10-role set follows the **Least-Privilege Principle** to keep your cloud environment safe. Here is why each role is needed:
> * `artifactregistry.writer`: To push finished container images.
> * `run.admin` & `cloudscheduler.admin`: To launch Cloud Run runners and cron schedules.
> * `iam.serviceAccountAdmin` & `iam.serviceAccountUser`: To create dedicated job service accounts.
> * `resourcemanager.projectIamAdmin` & `secretmanager.admin`: To grant document search permissions and secret access across projects.
> * `storage.admin` & `storage.objectAdmin`: To read/write Terraform state and mount shared SQLite cache buckets.

### 3. Grant Cross-Project IAM Admin (Cross-Project Deployment Prerequisite)
If your Discovery Engine datastores reside in a central Search Hub project separate from your compute runners (`$PROJECT_ID`), an administrator of the Search Hub project must authorize Cloud Build to bind IAM policies across the project boundary:

```bash
export DATASTORE_PROJECT_ID="my-search-hub-proj-01" # Update to your Search Hub project ID

gcloud projects add-iam-policy-binding $DATASTORE_PROJECT_ID \
  --member="serviceAccount:cd-pipeline-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/resourcemanager.projectIamAdmin"
```
> [!CAUTION]
> If you omit this step during cross-project deployments, remote Cloud Build runs will crash with `HTTP 403 Forbidden` when Terraform attempts to attach `roles/discoveryengine.editor` to your compute runners.

---

## ⚙️ Phase 3: Check Your `cloudbuild.yaml` Defaults

Open [cloudbuild.yaml](../cloudbuild.yaml) in your IDE and double-check that the `substitutions:` block at the bottom points to your clean project's storage bucket:

```yaml
substitutions:
  _REGION: 'us-central1'
  _REPO_NAME: 'gep-custom-connectors'
  # Make sure this matches your active Terraform state bucket!
  _TF_BACKEND_BUCKET: 'my-clean-deployment-proj-01-tf-state'
```

---

## 🛠️ Phase 4: Repositories (2nd gen) Setup & Trigger

Google Cloud Build **Repositories (2nd gen)** lets you securely link repositories from GitHub, GitHub Enterprise, GitLab, or Bitbucket.

### 1. Connect Your Host Repository (Prerequisite Step)
Before running any CLI trigger commands, let's authorize the connection in your browser:
1. Go to **Cloud Console** $\rightarrow$ **Cloud Build** $\rightarrow$ **Repositories (2nd gen)** (or click this shortcut: [Repositories 2nd Gen Console](https://console.cloud.google.com/cloud-build/repositories)).
2. Click **Create Host Connection**, choose your Git host (like GitHub or GitHub Enterprise), select region `us-central1`, and name your connection (e.g., `my-github-connection`).
3. Authorize the prompt and click **Link Repository** to attach `gep-custom-connectors`.

> [!CAUTION]
> If you skip this console connection step, the CLI trigger command below will fail with `FAILED_PRECONDITION: Repository mapping does not exist`.

### 2. Create Your Trigger via CLI
Now that your repo is linked, let's create the webhook trigger! 

To save precious CI/CD build minutes, we want Cloud Build to run *only* when real application code or infrastructure changes (like `pipelines/`, `src/`, `terraform/`, `Dockerfile`). We don't want it running when you're just updating wiki docs or notebook experiments!

We recommend using positive allowlist filtering (**`--included-files`**). Alternatively, you can use a denylist (**`--ignored-files`**):

```bash
export CONNECTION_NAME="my-github-connection" # Update to your 2nd gen connection name
export REPO_RESOURCE="projects/${PROJECT_ID}/locations/${REGION}/connections/${CONNECTION_NAME}/repositories/${REPO_NAME}"

# Option A: Positive Allowlist Filtering (Recommended)
gcloud builds triggers create github \
  --name="custom-connectors-main-cd" \
  --region=$REGION \
  --repository=$REPO_RESOURCE \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${SA_EMAIL}" \
  --substitutions=_REGION=$REGION,_REPO_NAME=$REPO_NAME,_TF_BACKEND_BUCKET=${PROJECT_ID}-tf-state \
  --included-files="pipelines/**,src/**,terraform/**,main.py,Dockerfile,pyproject.toml,uv.lock,cloudbuild.yaml"

# Option B: Negative Denylist Filtering (Alternative)
# --ignored-files="docs/**,README.md,LICENSE,*.md,setup.ipynb,*.ipynb,tests/**,ai-context/**,.env*,.git*"
```

---

## ✅ Phase 5: Verify & Test Your Pipeline

### 1. Run a Manual Dry-Run
Want to test your Cloud Build setup right now without making a Git commit? Run this manual submit command:
```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_REPO_NAME=$REPO_NAME,_TF_BACKEND_BUCKET=${PROJECT_ID}-tf-state
```

### 2. Run Your Trigger on Demand
You can also trigger your newly created webhook programmatically via CLI:
```bash
gcloud builds triggers run custom-connectors-main-cd \
  --region=$REGION \
  --branch="main"
```
> [!NOTE]
> In typical team workflows, direct pushes to `main` are disabled. Instead, this automated trigger executes silently in the background whenever a teammate's Pull Request is approved and merged into `main`.

### 3. Watch the Logs
Stream real-time build status updates directly in your terminal:
```bash
gcloud builds list --limit=5 --format="table(id,status,createTime,source.subrepo.branch)"
```

---

## 🧭 Navigation
* 🏠 [Wiki Home](README.md)
* 📐 [Architecture Philosophy](Architecture-Philosophy.md)
* 🚀 [First-Time Deployment Guide](First-Time-Deployment-Guide.md)
