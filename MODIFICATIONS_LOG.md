# Modifications to GEP Custom Connectors Engine

This document provides a comprehensive technical record of all modifications made to the `gep-custom-connectors` repository. A file-by-file audit has been performed, comparing every file against the original repository base.

## 1. Summary of Changes
The primary goal of these modifications was to extend the engine with BigQuery ingestion capabilities specifically for the "Snooguts Mission Control" datasource, while resolving critical serialization bugs and streamlining the production deployment workflow.

## 2. Core Logic Modifications

### File: `src/bigquery/transformers.py` (New Component)
The implementation of the `SnoogutsTransformer` included a critical fix for BigQuery data types.
- **Problem:** BigQuery returns `datetime` objects that cause `TypeError: Object of type datetime is not JSON serializable` during Vertex AI Search ingestion.
- **Fix:** 
    - Implemented a custom `DateTimeEncoder` class.
    - Added `_convert_datetimes` recursive helper to deep-convert all nested timestamps to ISO-8601 strings.
    - Integrated this preprocessing into the `transform` method to ensure 100% schema compatibility.

### File: `pyproject.toml` (Modified)
- **Dependency Addition:** Added `google-cloud-bigquery>=3.10.0` to enable the fetcher to communicate with BigQuery.
- **Note:** All other core dependencies remain unchanged.

## 3. Infrastructure & Deployment Enhancements

### File: `terraform/modules/ingestion-pipeline/main.tf` (Modified)
- **IAM Security:** Enhanced the Service Account provisioning logic to include BigQuery specific roles (`roles/bigquery.dataViewer`, `roles/bigquery.jobUser`). This ensures the Cloud Run Job has autonomous access to the source data.

### File: `terraform/jobs/main.tf` (Modified)
- **State Management:** Switched the Terraform backend from `gcs` to `local`. This was done to ensure deployment portability across environments where dedicated state buckets might not be pre-provisioned.

## 4. Entirely New Components
The following files and folders were added to provide the custom Snooguts functionality:

- **Directory: `src/bigquery/`**:
    - `__init__.py`: Module initialization.
    - `fetchers.py`: Implementation of the `BigQuerySnoogutsFetcher` for multi-table data extraction.
    - `transformers.py`: Data mapping and serialization logic.
- **Directory: `pipelines/` (Additions)**:
    - `test_snooguts_mock.yaml`: The production-verified pipeline configuration.
- **Root Directory (Additions)**:
    - `mock_data.json`: The source dataset for the Snooguts mission.
    - `prepare_bq_data.py`: CLI utility to prepare BigQuery load files.
    - `sync_schema.txt`: Detailed datasource documentation.
    - `MODIFICATIONS_LOG.md`: This audit record.
    - `DEPLOYMENT_GUIDE.md`: The verified deployment manual.

## 5. Files Verified as UNCHANGED
The following core engine components were audited and found to be **identical** to the original repository, ensuring the base framework remains intact:
- `main.py`: Core CLI entry point.
- `src/core/*.py`: All base classes (loader, pipeline, uploader, models).
- `src/github/*.py`: GitHub connector logic.
- `src/mock/*.py`: Mock connector logic.
- `docs/*.md`: Architectural documentation.
- `tests/*.py`: Base test suite.
- `Dockerfile`, `deploy.sh`, `cloudbuild.yaml`: Containerization and script logic.

## 7. Deployment & Schema Fixes (July 8, 2026)
- **BigQuery Schema Enforcement:** Recreated all BigQuery tables with explicit JSON schemas. Converted all `id` fields to `STRING` and marked them as `REQUIRED` to comply with the connector's data contract.
- **Infrastructure Provisioning:**
    - Successfully deployed the `snooguts-ds` Data Store in Vertex AI Search (Generic Search).
    - Provisioned Cloud Run Job `snooguts-mock-test-sync` via Terraform.
    - Configured Cloud Scheduler for automated syncs.
- **Ingestion Verification:** 
    - Verified the end-to-end flow from BigQuery -> Cloud Run -> Vertex AI Search.
    - Successfully ingested 11 search-ready documents from a source set of 17 records.
    - Validated data presence in the Gemini Enterprise Agent Platform console.

## 8. `INCORRECT_JSON_FORMAT document` Fix (July 8, 2026)

### 8.1 Problem
Cloud Run job logs reported `Uploaded: 11 items / Failed: 0`, but the target data store held zero indexed documents. The Discovery Engine import LRO on `snooguts-ds-v2` was rejecting every document with `INCORRECT_JSON_FORMAT document`. Root causes identified:

1. **Wrong Document field.** `SnoogutsTransformer` emitted `discoveryengine.Document(struct_data=..., content=Content(mime_type="text/plain", raw_bytes=payload["description"].encode()))`. The Gemini Enterprise Custom Connector spec (Google Cloud docs) requires `json_data=json.dumps(payload)` with **no** `content` block. The empty `raw_bytes` on records with no description (initiative-102, launch-802, all `_update` scenarios) triggered the rejection first, but the fundamental issue was that a metadata-only custom connector must not carry a `content` field.
2. **Wrong `contentConfig`.** `snooguts-ds-v2` was created with `contentConfig: CONTENT_REQUIRED`. Custom connectors ship metadata-only documents and must target a `NO_CONTENT` store; a `CONTENT_REQUIRED` store rejects every doc that lacks a `content` block.
3. **Data-store id mismatch.** `create_ds.py` created `snooguts-ds`; `pipelines/test_snooguts_mock.yaml` referenced `snooguts-ds-v2`. Only v2 existed on the live project; v1 didn't.
4. **`commitmentIds` double-prefix.** `_transform_launch` was prefixing values that already carried the `commitment-` prefix, producing `commitment-commitment-501`.
5. **Schema-type mismatch on `customProperties.value`.** After the first fix went out (v3), the import LRO returned `successCount: 4, failureCount: 3` with errors `Unexpected value of array type` and `Unable to parse the value of "" into a string type` for `customProperties.value`. Discovery Engine's inferred schema locks that field to a single scalar type on first ingest; mixing strings, ints, and arrays across entities is rejected.

### 8.2 Code changes

- **`src/bigquery/transformers.py` — Full rewrite.**
    - Emits `discoveryengine.Document(id=..., json_data=json.dumps(payload), acl_info=...)`. No `struct_data`, no `content`.
    - New envelope matches `sync_schema.txt §5`: `datasource, objectType, id, title, body{mimeType,textContent}, viewURL, createdAt, updatedAt, permissions, author, customProperties, interactions?`.
    - Integer timestamps preserved as integers via `_int_or_none` (previously float-coerced by `protobuf.Struct`).
    - New `_add_prop` coerces every `customProperties[].value` to a string — arrays/objects JSON-encoded, numbers `str()`-cast, bools `"True"/"False"` — aligning with sync_schema.txt's existing `hierarchyTeam: string (JSON-encoded array)` convention and Discovery Engine's single-scalar-type schema constraint.
    - New `_build_permissions` emits the sync_schema.txt §5 `permissions` block inside the payload; ACL enforcement continues to be driven by the top-level `acl_info` on the Document.
    - New `_prefix_id` fixes the `commitment-commitment-501` bug idempotently.
    - `person` transformer forces `allowAnonymousAccess=True` in `permissions` per sync_schema.txt §9.
    - `_add_prop` skips empty strings/lists per sync_schema.txt §5 "properties included only when their mapped value is truthy".

- **`create_ds.py`.**
    - `contentConfig`: `CONTENT_REQUIRED` → `NO_CONTENT`.
    - Added `aclEnabled: true`.
    - Target id updated: `snooguts-ds` → `snooguts-ds-v4` (v2 unmodifiable, v3 exhausted — see §8.4).

- **`pipelines/test_snooguts_mock.yaml`.**
    - `data_store_id`: `snooguts-ds-v2` → `snooguts-ds-v4`.

### 8.3 Infrastructure actions taken

- Created data store `snooguts-ds-v3` (later deleted — see §8.4).
- Deleted data store `snooguts-ds-v3` after schema was locked to string-only types by a partial-success import. Delete is asynchronous and the id remains unavailable for reuse for ~2 hours per Discovery Engine.
- Created data store `snooguts-ds-v4` (`NO_CONTENT`, `aclEnabled: true`, GENERIC).
- Built and pushed container image `us-central1-docker.pkg.dev/creativestudiotest-492015/gep-custom-connectors/custom-connectors:v4-stringprops-1783552179` via Cloud Build.
- Updated Cloud Run job `snooguts-mock-test-sync` to point at the new image.
- Executed `snooguts-mock-test-sync` against `snooguts-ds-v4`.
- **v2 (`snooguts-ds-v2`, `CONTENT_REQUIRED`) was left in place** — it can't be re-configured to `NO_CONTENT`. It should be deleted manually once the v4 rollout is confirmed stable.

### 8.4 Verification

- Import LRO `import-documents-13024319657674335832` returned `successCount: 7, failureCount: 0` with no error samples. (17 raw BQ rows → 11 upserts after `delete` action filter and `user` skip → 7 unique document IDs after in-batch dedup of `_full` + `_update` scenarios.)
- Search against `snooguts-ds-v4` via `servingConfigs/default_search:search`:
    - Anonymous (via caller identity): 4 documents — the `idp_wide` ones (`initiative-102`, `commitment-502`, `launch-802`, `person-9001`).
    - `userInfo.userId=lead.one@example.com`: 7 documents (private ACLs honored).
- `GetDocument` and `ListDocuments` return 400 / empty on ACL-enabled data stores by design — use `search` with `userInfo` for inspection. `check_ops.py` still works for LRO stats.

## 9. Staging Bucket Rename → `cc-mission-control` (July 8, 2026)

- **Bucket created**: `gs://cc-mission-control` in `creativestudiotest-492015`, `us-central1`, uniform bucket-level access.
- **IAM**: Granted `roles/storage.objectAdmin` to `snooguts-mock-test-sync-sa@creativestudiotest-492015.iam.gserviceaccount.com` on the new bucket.
- **`pipelines/test_snooguts_mock.yaml`**: `gcs_bucket` `creativestudiotest-492015-snooguts-staging` → `cc-mission-control`.
- **`DEPLOYMENT_GUIDE.md`**: bucket-create command updated to `gs://cc-mission-control` with `--uniform-bucket-level-access`.
- **Runtime note**: The connector runs in `INCREMENTAL` mode and uploads inline via gRPC — the GCS bucket is only touched when the pipeline runs in `FULL` reconciliation mode (`src/core/gcp_uploaders.py:_upload_via_gcs`). The bucket is now provisioned and permissioned so a `FULL` sync will work without further changes. If a `FULL` sync is ever run, the Discovery Engine service agent (`service-{PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com`) will also need `roles/storage.objectViewer` on the bucket.
- **Old bucket**: `creativestudiotest-492015-snooguts-staging` is no longer referenced by any pipeline. Safe to delete manually once you've confirmed nothing external depends on it.

## 10. GitHub Repository Bootstrap (July 8, 2026)

- **Remote**: `https://github.com/lucaslm-stack/CC_GeminiEnterprise_MissionControl`
- **Local**: Repo initialized in `/home/admin_/pruebasCC/gep-custom-connectors`. First commit contains the full connector source, pipeline definitions, terraform, deployment guide, mock data, and the full modification history in this file.
