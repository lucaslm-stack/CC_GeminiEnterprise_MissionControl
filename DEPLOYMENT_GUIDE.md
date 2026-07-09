# Deployment Guide: Gemini Enterprise Custom Connector

End-to-end deploy of the Snooguts Mission Control connector into Google Cloud + Gemini Enterprise.

This is the guide as of the latest revision (v5). If you've followed an older revision, the "What changed" appendix at the bottom lists the migrations.

## Prerequisites
- Google Cloud project with billing enabled.
- `gcloud` SDK installed and authenticated as an owner / editor.
- `bq` CLI (ships with gcloud).
- `terraform` v1.0.0+ (optional — Cloud Run Job creation is scripted below without it, but the repo ships a terraform module if you want scheduled runs + IAM handled declaratively).
- Python 3.9+ for `prepare_bq_data.py`.

## Step 1: Project environment setup

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"
export DATASTORE_ID="snooguts-ds-v4"       # keep in sync with pipelines/test_snooguts_mock.yaml
export STAGING_BUCKET="cc-mission-control"  # keep in sync with pipelines/test_snooguts_mock.yaml
export DATASET_ID="snooguts_mock"

gcloud config set project $PROJECT_ID
gcloud services enable \
    discoveryengine.googleapis.com \
    bigquery.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    cloudresourcemanager.googleapis.com \
    datacatalog.googleapis.com \
    iam.googleapis.com
```

## Step 2: BigQuery source setup

The connector reads Snooguts entity rows from BigQuery and expects a per-row materialized ACL (see `sync_schema.txt §5.1`). It runs as a single service account and cannot derive per-user visibility from BigQuery IAM / row-level security / column-level security — those must be flattened into `allowedUsers` / `allowedGroups` at load time.

```bash
# 1. Create the dataset
bq mk --dataset "$PROJECT_ID:$DATASET_ID"

# 2. Split the shipped mock data into per-table JSONL files
python3 prepare_bq_data.py

# 3. Load the tables. Autodetect infers the schema (including allowedGroups,
#    which is a REPEATED STRING added in v5).
for table in initiatives commitments launches person user; do
  bq load --autodetect --source_format=NEWLINE_DELIMITED_JSON \
    "${DATASET_ID}.${table}" "bq_load/${table}.jsonl"
done
```

### 2.1 ACL contract (BQ → connector)

Every row participating in a sync MUST expose these columns:

| Column          | Type              | Required | Notes                                                                                     |
|-----------------|-------------------|----------|-------------------------------------------------------------------------------------------|
| `id`            | STRING            | yes      | Sanitized into the Discovery Engine document id (`entity-{id}`).                          |
| `private`       | BOOL              | yes      | `false` → idp_wide readable. `true` → readers come from the allowed lists below.          |
| `allowedUsers`  | ARRAY<STRING>     | optional | Google-recognized user emails.                                                            |
| `allowedGroups` | ARRAY<STRING>     | optional | Group ids. See §2.2 for the two accepted formats.                                         |
| `ownerEmail`    | STRING            | optional | Last-resort principal when a private row has no allowedUsers / allowedGroups.             |

Enforcement in the transformer:
- `private=true` **and** no principals **and** no `ownerEmail` → the row is DROPPED with a warning. This is the fail-safe against a private row leaking through with implicit anyone-can-read access.

### 2.2 `allowedGroups` — two accepted formats

| Format                                       | When to use                                                                                                      | Requires IMS? |
|----------------------------------------------|------------------------------------------------------------------------------------------------------------------|---------------|
| `group@example.com`                          | Real Google Group in your Workspace / Cloud Identity tenant. Membership resolved by Gemini Enterprise at query time. | No            |
| `external_group:<name>`                       | Non-Google groups (e.g. legacy team labels, wp-admins). Membership resolved via an Identity Mapping Store bound to the data store. | **Yes** (see Step 3.2) |

If you send `external_group:` values without a bound IMS, Discovery Engine rejects the whole document with "Request contains an invalid argument" at import time.

### 2.3 Column-level security (Data Catalog policy tags)

If any BQ column is protected by a Data Catalog policy tag, the fetcher detects it (`INFO connector.bigquery.fetchers: policy-tagged columns detected: ...`) and attempts a plain `SELECT *`. On a fine-grained denial the fetcher retries with `SELECT * EXCEPT (<tagged_cols>)` and logs a WARN. The tagged column arrives at the transformer as an absent key (the schema-drift detector will list it at INFO). To include the values, grant the ingestion SA `roles/datacatalog.categoryFineGrainedReader` on the tag.

### 2.4 Row-level security

BigQuery RLS filters rows based on the *querying* identity — the ingestion SA. There is no way for the connector to enumerate "for this row, which end-users would have passed the RLS predicate." If you use RLS, mirror the effective allowed principals into `allowedUsers` / `allowedGroups` at your BQ load step (typical pattern: an `INSERT ... SELECT` from a canonical permissions table).

## Step 3: Vertex AI Search infrastructure

### 3.1 Staging GCS bucket

Used by the connector only when `reconciliation_mode: FULL` (the current pipeline runs `INCREMENTAL` and uploads inline). Still provision it so a future FULL sync works without a scramble.

```bash
gcloud storage buckets create "gs://$STAGING_BUCKET" \
    --project=$PROJECT_ID --location=$REGION --uniform-bucket-level-access

# Grant the ingestion SA object-admin on the bucket.
gcloud storage buckets add-iam-policy-binding "gs://$STAGING_BUCKET" \
    --member="serviceAccount:snooguts-mock-test-sync-sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

# If you ever run in FULL mode, the Discovery Engine service agent also needs read access:
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding "gs://$STAGING_BUCKET" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
```

### 3.2 (Optional) Identity Mapping Store

**Only** required if you use `external_group:` in `allowedGroups`. Skip this section if all your groups are real Google Groups.

```bash
# Create the IMS
TOKEN=$(gcloud auth print-access-token)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "X-Goog-User-Project: $PROJECT_ID" -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/identityMappingStores?identityMappingStoreId=snooguts-ims" \
  -d '{}'

# Load identity mappings — each entry maps an external identity to a Google user / group.
# See create_custom_connector documentation.pdf pg. 16-17 for the entry format.

# The IMS resource name is required when you create the data store in Step 3.3.
export IMS_NAME="projects/$PROJECT_ID/locations/global/identityMappingStores/snooguts-ims"
```

### 3.3 Data store

Custom-connector documents carry metadata via `json_data` only (no `content` block), so the data store MUST be created with `contentConfig: NO_CONTENT`. `CONTENT_REQUIRED` rejects every doc with `INCORRECT_JSON_FORMAT`.

```bash
TOKEN=$(gcloud auth print-access-token)

# The included create_ds.py handles the standard case (no IMS). Just run:
python3 create_ds.py
```

Or, manually via curl:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "X-Goog-User-Project: $PROJECT_ID" -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/collections/default_collection/dataStores?dataStoreId=$DATASTORE_ID" \
  -d '{
    "displayName": "Snooguts Mock Data Store",
    "industryVertical": "GENERIC",
    "contentConfig": "NO_CONTENT",
    "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
    "aclEnabled": true
  }'
```

If you're using an IMS, add `"identityMappingStore": "'"$IMS_NAME"'"` to the request body — the binding is fixed at creation time and cannot be added later.

## Step 4: Container image

```bash
# 1. Artifact Registry repo
gcloud artifacts repositories create gep-custom-connectors \
    --repository-format=docker --location=$REGION

# 2. Build + push
gcloud builds submit \
    --tag "$REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest" \
    --timeout=15m .
```

## Step 5: Cloud Run Job

Two paths — pick one.

### 5a. Terraform (recommended for ongoing operation)

Includes the SA, IAM bindings for BigQuery / Discovery Engine / GCS / logging, Cloud Scheduler trigger, and the GCS-mounted cache volume.

```bash
export IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest"

# terraform/jobs/main.tf uses a local backend by default in this repo.
terraform -chdir=terraform/jobs init -reconfigure
terraform -chdir=terraform/jobs apply -auto-approve \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="image_uri=$IMAGE_URI"
```

### 5b. gcloud (fastest for a one-off image swap)

```bash
gcloud run jobs update snooguts-mock-test-sync \
  --region=$REGION --project=$PROJECT_ID --image="$IMAGE_URI"
```

## Step 6: Pipeline configuration

`pipelines/test_snooguts_mock.yaml` is the runtime config the container loads. Notable knobs:

```yaml
pipeline:
  fetcher:
    class: "src.bigquery.fetchers.BigQuerySnoogutsFetcher"
    params:
      project_id: "<your project>"
      dataset_id: "snooguts_mock"
      # Auto-discovery: any table in the dataset matching this regex is
      # included. Rows whose entityType has no transformer branch are dropped
      # with a warning (visible in logs). To lock down explicitly, replace
      # `table_pattern` with `tables: [...]`.
      table_pattern: ".*"
  uploader:
    class: "src.core.gcp_uploaders.GoogleCloudDiscoveryEngineDocumentUploader"
    params:
      project_id: "<your project>"
      data_store_id: "snooguts-ds-v4"        # matches Step 3.3
      location: "global"
      branch_id: "default_branch"
      gcs_bucket: "cc-mission-control"       # matches Step 3.1
deployment:
  reconciliation_mode: "INCREMENTAL"         # FULL requires the GCS bucket to be reachable
```

## Step 7: Trigger and verify

```bash
gcloud run jobs execute snooguts-mock-test-sync --region=$REGION --wait

# The connector logs count-of-sent, not count-of-indexed. Check the import LRO
# for the true ingest result:
EXEC=$(gcloud run jobs executions list --job=snooguts-mock-test-sync \
  --region=$REGION --limit=1 --format='value(name)')
LRO=$(gcloud logging read "resource.type=cloud_run_job AND labels.\"run.googleapis.com/execution_name\"=$EXEC" \
  --limit=200 --format="value(textPayload)" | grep -oE "import-documents-[0-9]+" | head -1)

TOKEN=$(gcloud auth print-access-token)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: $PROJECT_ID" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_NUMBER/locations/global/collections/default_collection/dataStores/$DATASTORE_ID/branches/0/operations/$LRO"
```

Expected on success: `metadata.successCount == number of unique document ids sent`, no `response.errorSamples`.

### 7.1 Inspecting documents

`GetDocument` and `ListDocuments` return 400 / empty on ACL-enabled data stores by design. Use the search API with a `userInfo.userId` that matches an ACL:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: $PROJECT_ID" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/collections/default_collection/dataStores/$DATASTORE_ID/servingConfigs/default_search:search" \
  -d '{"query":"","pageSize":20,"userInfo":{"userId":"lead.one@example.com"}}'
```

Anonymous (no `userInfo`) returns only `idp_wide` documents.

## Step 8: Operational signals to watch

The transformer and fetcher emit named log events specifically for schema drift, ACL enforcement, and CLS handling. Wire alerts on these strings if this pipeline is critical:

| Level   | Log source                              | Substring to alert on                                           | Meaning                                                             |
|---------|-----------------------------------------|-----------------------------------------------------------------|---------------------------------------------------------------------|
| WARN    | `connector.bigquery.transformers`       | `Dropping doc `                                                 | A row was private but had zero principals — ACL contract violated.  |
| WARN    | `connector.bigquery.transformers`       | `No transformer branch for entityType=`                         | A BQ table was discovered whose entityType nothing knows about.     |
| WARN    | `connector.bigquery.fetchers`           | `read denied on policy-tagged column(s)`                        | SA lacks fineGrainedReader; those columns are being excluded.       |
| INFO    | `connector.bigquery.transformers`       | `Schema drift: entity=`                                         | A new BQ column exists that no property mapping consumes.           |
| INFO    | `connector.bigquery.fetchers`           | `Fetcher auto-discovered `                                      | Sanity-check the discovered table list matches expectations.        |

## Appendix: What changed since earlier revisions

- **v3 → v4 (data store)**: `snooguts-ds-v2` used `contentConfig: CONTENT_REQUIRED` — every document was rejected with `INCORRECT_JSON_FORMAT`. Replaced by `snooguts-ds-v4` with `NO_CONTENT` + `aclEnabled: true`.
- **Bucket rename**: `creativestudiotest-492015-snooguts-staging` → `cc-mission-control` (globally unique-ish, no project prefix). Old bucket can be deleted after confirming no external consumer uses it.
- **Transformer rewrite**: emits `json_data` (not `struct_data`), drops the always-empty `content` block, stringifies every `customProperties.value` (Discovery Engine locks that field to a single scalar type at first ingest), fixes the `commitment-commitment-<id>` double-prefix.
- **Solution A**: `allowedGroups` principals now flow into `AclInfo`. Legacy rows without the column continue to work.
- **Solution B**: transformer logs schema drift — any unmapped BQ column is surfaced at INFO instead of silently dropped.
- **Solution C**: fetcher auto-discovers tables via `table_pattern` (default `.*`). Unmapped entity types are warned about.
- **Solution D.1**: ACL is a hard contract — private rows with no principals are dropped, not shipped with anyone-can-read.
- **Solution E**: fetcher detects Data Catalog policy tags per table and gracefully falls back to `SELECT * EXCEPT (...)` on fine-grained denial.
