# Team Walkthrough — BigQuery/GCP Custom Connector

Onboarding doc for teammates. Read this first, then jump into
[`DEPLOYMENT_GUIDE.md`](./DEPLOYMENT_GUIDE.md) when you're ready to run it.
The [`MODIFICATIONS_LOG.md`](./MODIFICATIONS_LOG.md) has the full change
history (what broke, what got fixed, why the current shape looks the way it
does).

Sections:
1. What the connector does
2. How the code is organized
3. How data flows through it (fetch → transform → upload)
4. **BigQuery-side security model — what governs what the connector sees**
5. **How the ACL contract maps onto the Gemini Enterprise Custom Connector**
6. Step-by-step reproduction (scripts to run, files to edit, order of execution)
7. Common issues + where to look
8. Sibling repo: Postgres/AWS variant

---

## 1. What the connector does

Reads Snooguts Mission Control rows from **BigQuery** and imports them into
a **Discovery Engine data store** as Gemini-Enterprise-Custom-Connector
documents. Each row becomes one document with:

- A shared metadata envelope (title, body, timestamps, viewURL, author,
  custom properties) — see `sync_schema.txt §5`.
- A per-document ACL (`acl_info` on the Document) — governs who can retrieve
  the document via Gemini Enterprise search.

The connector runs on a schedule (Cloud Scheduler → Cloud Run Job), never
talks to end-user identities, and re-imports the full working set
INCREMENTALly on each run.

The target data store is `snooguts-ds-v5`. It's bound to an Identity
Mapping Store (`snooguts-ims`) so `external_group:` principals resolve at
query time. Both are provisioned idempotently by `create_ds.py`.

---

## 2. How the code is organized

```
main.py                    CLI entry point. Reads PIPELINE_CONFIG env var
                           and runs the named pipeline yaml. Also handles
                           the GCS-mounted cache round-trip.
pipelines/
  test_snooguts_mock.yaml            The production pipeline.
  live_github_*.yaml.disabled        Reference — kept for the GitHub connector
                                     shape; not active in this deployment.
src/core/
  base.py                  BaseDocumentFetcher / Transformer / Uploader.
  loader.py                Turns a yaml into an executable ConnectorPipeline
                           via reflection on the class-path strings.
  pipeline.py              The fetch → transform → upload runner.
  models.py                RawPayload, PipelineContext, SyncResult.
  gcp_uploaders.py         GoogleCloudDiscoveryEngineDocumentUploader.
src/bigquery/
  fetchers.py              BigQuerySnoogutsFetcher — the source-side logic.
  transformers.py          SnoogutsTransformer — row → Discovery Engine Document.
src/github/                Secondary GitHub connector. Not on the critical
                           path for Snooguts; useful as a reference for how
                           other sources fit the same core.
src/mock/                  In-process mock fetcher/transformer/uploader,
                           used by the tests to exercise the pipeline
                           without cloud creds.
create_ds.py               GCP side: IMS + snooguts-ds-v5 + schema PATCH.
                           Idempotent — safe to re-run.
prepare_bq_data.py         Split mock_data.json into per-table JSONL files
                           for `bq load`.
check_ops.py               Poll a Discovery Engine LRO by id.
list_docs_v2.py            Peek at what's in the data store (ACL-aware).
search_docs_v2.py          Fire an ACL-aware search.
deploy.sh                  Local deploy runner: validates pipelines, builds
                           + pushes the image, applies terraform.
cloudbuild.yaml            Cloud Build config used by `gcloud builds submit`.
terraform/jobs/            Cloud Run Job + IAM + Scheduler for this pipeline.
terraform/modules/ingestion-pipeline/   Reusable module the jobs/ project consumes.
bq_load/                   Generated. Per-table JSONL files ready for `bq load`.
DEPLOYMENT_GUIDE.md        End-to-end deploy runbook.
MODIFICATIONS_LOG.md       Change history with root-cause writeups for every
                           production-visible bug + fix.
sync_schema.txt            Payload contract.
```

---

## 3. How data flows through it

```
   +------------------------+
   |  Cloud Run Job         |
   |                        |
   |  main.py               |
   |    ├─ (optional) sync cache from GCS mount
   |    └─ build_pipeline_from_yaml(PIPELINE_CONFIG)
   |         └─ instantiates fetcher / transformer / uploader by class path
   |                        |
   |  ConnectorPipeline.run(context)
   |    ├─ FETCH:  BigQuerySnoogutsFetcher.fetch(ctx)
   |    │            └─ _resolve_tables       (auto-discovery, Solution C)
   |    │            └─ _build_query          (checks for policy tags, Solution E)
   |    │            └─ _run_with_cls_fallback (SELECT * → SELECT * EXCEPT on 403)
   |    │            └─ yields RawPayload per row
   |    ├─ TRANSFORM:  SnoogutsTransformer.transform(raw, ctx)
   |    │            ├─ filter delete actions
   |    │            ├─ dispatch on entityType
   |    │            ├─ _build_permissions   → JSON envelope permissions
   |    │            ├─ _build_acl_info       → Document.AclInfo
   |    │            ├─ _add_prop             → coerce all customProperty values to strings
   |    │            ├─ _log_schema_drift     → INFO on unmapped keys
   |    │            └─ Document(id, json_data, acl_info)      # no `content` block
   |    └─ UPLOAD:  GoogleCloudDiscoveryEngineDocumentUploader.upload(items, ctx)
   |                 ├─ INCREMENTAL → inline gRPC ImportDocumentsRequest per batch of 100
   |                 └─ FULL → stage as JSONL in GCS bucket, then ImportDocumentsRequest(gcs_source)
   +------------------------+
```

Two things worth internalizing:

- **The fetcher and transformer are decoupled from the source AND the sink.**
  The interfaces in `src/core/base.py` are abstract — swapping BigQuery for
  Postgres is a fetcher swap only (see §8). Swapping Discovery Engine for a
  local file is an uploader swap.
- **The transformer is the ACL/security boundary.** By the time a Document
  leaves the transformer, its `acl_info` is authoritative. Nothing
  downstream re-checks anything against BigQuery IAM / RLS / CLS.

---

## 4. BigQuery-side security model — what governs what the connector sees

The connector runs as **one identity** — a Cloud Run Job service account
(default: `snooguts-mock-test-sync-sa`). It cannot enumerate per-user
visibility against BigQuery. Everything Discovery Engine will use for
ACL-aware search MUST arrive in the row itself (`private`,
`allowedUsers`, `allowedGroups`, `ownerEmail` — see `sync_schema.txt §5.1`).

But there are three BQ-side machines that can gate what the connector's
identity itself can *see*. Understand each before adding a new source table:

### 4.1 IAM (project / dataset / table level)

- `roles/bigquery.dataViewer` on the dataset → the SA can query the tables.
- `roles/bigquery.jobUser` on the project → the SA can *run* query jobs.
- If either is missing, the fetcher fails at the first `client.query(...)`
  with a 403 and records a `BigQueryFetcher` error in `SyncResult.errors`.

Terraform (`terraform/modules/ingestion-pipeline/main.tf`) provisions these
roles on the SA at deploy time — you shouldn't have to touch this unless
you're pointing the connector at a dataset in another project.

### 4.2 Row Access Policies (RLS)

- BigQuery RLS filters rows based on the *querying identity* — the
  connector's SA.
- If a table has RLS enabled and the SA is not on the policy allow-list,
  the connector sees the empty set. No error, no warning — just fewer rows.
- **There is no way to introspect "for this row, which end-users would
  have passed the RLS predicate."** RLS on the source therefore does NOT
  translate to `allowedUsers` / `allowedGroups` on the Discovery Engine
  document.
- If you use RLS on a source table, materialize the effective allowed
  principals into `allowedUsers` / `allowedGroups` at your BQ load step
  (typical pattern: an `INSERT ... SELECT` from a canonical permissions
  table).

### 4.3 Column-Level Security (Data Catalog policy tags)

- A BQ column can be tagged with a Data Catalog policy tag. Roles that
  lack `roles/datacatalog.categoryFineGrainedReader` on the tag get a
  fine-grained denial on `SELECT *`.
- The fetcher handles this end-to-end (`_run_with_cls_fallback`):
  1. Inspects the table schema for policy tags → logs at INFO which
     columns are tagged.
  2. Tries `SELECT *`.
  3. On a 403 whose message mentions "policy" or "fine-grained", retries
     with `SELECT * EXCEPT (<tagged_cols>)` and logs at WARN.
  4. Excluded columns arrive at the transformer as absent keys → the
     schema-drift detector lists them at INFO. **Silent data loss is
     impossible.**
- To include the values in the output, grant the SA
  `roles/datacatalog.categoryFineGrainedReader` on the tag.

### 4.4 Row-shape ACL columns (`private`, `allowedUsers`, `allowedGroups`, `ownerEmail`)

- These are the *only* thing that governs end-user visibility at search
  time. See `sync_schema.txt §5.1` for the contract and enforcement rules.
- **A private row with no principals and no ownerEmail is DROPPED with a
  WARN.** This is intentional fail-safe behavior — rather than shipping a
  row with implicit anyone-can-read access, the connector refuses it.
  Materialize the ACL at the BQ load step.

---

## 5. How the ACL contract maps onto the Gemini Enterprise Custom Connector

The Custom Connector protocol is opinionated. Every document you send must:

1. Use `discoveryengine.Document(id, json_data=..., acl_info=...)`.
   **No `content` block.** Custom connectors are metadata-only.
   ([MODIFICATIONS_LOG §8.1](./MODIFICATIONS_LOG.md#81-problem) — original
   `INCORRECT_JSON_FORMAT` incident.)
2. Land in a data store with `contentConfig=NO_CONTENT`. `CONTENT_REQUIRED`
   rejects every document. `snooguts-ds-v2` was recreated as
   `snooguts-ds-v4` / `-v5` for exactly this reason.
3. Carry the shared JSON envelope (`sync_schema.txt §5`) — including a
   `permissions` block INSIDE the payload for downstream consumers, on top
   of the top-level `acl_info` that governs Discovery Engine's own ACL
   check.
4. Have `customProperties[].value` as a plain string. Discovery Engine's
   schema auto-inference otherwise locks the field to the type of the first
   value it sees and rejects everything that follows.
   - The transformer coerces all values via `_add_prop`.
   - `create_ds.py` PATCHes `schemas/default_schema` at creation time to
     explicitly declare `customProperties[].value: type=string` with no
     `format` hint, so datetime auto-detection can't lock the schema to a
     datetime-parseable type on the first date-shaped value it processes.
     ([MODIFICATIONS_LOG §12.3](./MODIFICATIONS_LOG.md#123-explicit-schema-patch--sidesteps-invalid-datetime-auto-inference).)
5. Encode ACL via `Document.AclInfo`:
   - `readers[0].idp_wide=True` — public documents. Any user in the IdP
     tenant can retrieve.
   - `readers[0].principals=[Principal(user_id=…), Principal(group_id=…)]`
     — restricted. Discovery Engine cross-checks the caller's identity
     against these principals at search time.
   - `group_id` values prefixed `external_group:` need an **IMS** bound to
     the data store at creation time. The binding is IMMUTABLE — this is
     why we couldn't retrofit v4 and had to create v5.
     ([MODIFICATIONS_LOG §12.2](./MODIFICATIONS_LOG.md#122-new-data-store-snooguts-ds-v5-with-ims-binding).)

Import path:
- INCREMENTAL (default): batches of 100 sent inline via gRPC
  `ImportDocumentsRequest(inline_source=..., reconciliation_mode=INCREMENTAL)`.
- FULL: writes to a JSONL in GCS (`gs://cc-mission-control/stage/<run_id>.jsonl`)
  then `ImportDocumentsRequest(gcs_source=...)`. The Discovery Engine
  service agent needs `roles/storage.objectViewer` on the bucket for FULL
  syncs; INCREMENTAL never touches the bucket.

Verification path (post-import):
- The connector logs count-of-sent, not count-of-indexed. Pull the
  `import-documents-...` LRO id from the logs and check `successCount` /
  `failureCount` / `errorSamples` on it (`check_ops.py`).
- `GetDocument` / `ListDocuments` return 400 / empty on ACL-enabled data
  stores by design. Use `search:search` with `userInfo.userId` (or
  `search_docs_v2.py`) to inspect.

---

## 6. Step-by-step reproduction

**Prereqs:** GCP project, billing enabled, `gcloud` authenticated as owner
/ editor, `bq` CLI, Terraform ≥ 1.0, Python 3.9+, Docker.

Set your working env:

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"
export DATASTORE_ID="snooguts-ds-v5"
export STAGING_BUCKET="cc-mission-control"
export DATASET_ID="snooguts_mock"

gcloud config set project $PROJECT_ID
gcloud services enable \
    discoveryengine.googleapis.com bigquery.googleapis.com \
    run.googleapis.com cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com cloudbuild.googleapis.com \
    cloudresourcemanager.googleapis.com datacatalog.googleapis.com \
    iam.googleapis.com
```

**Step 1 — Load BQ tables** (see `DEPLOYMENT_GUIDE.md §2`):

```bash
bq mk --dataset "$PROJECT_ID:$DATASET_ID"
python3 prepare_bq_data.py
for t in initiatives commitments launches person user; do
  bq load --autodetect --source_format=NEWLINE_DELIMITED_JSON \
    "${DATASET_ID}.${t}" "bq_load/${t}.jsonl"
done
```

Files to edit: none for the mock. For your own tables, keep the ACL
columns (`sync_schema.txt §5.1`) and let auto-discovery pick up the rest.

**Step 2 — Provision the GCS staging bucket** (optional for INCREMENTAL,
required for FULL — see `DEPLOYMENT_GUIDE.md §3.1`):

```bash
gcloud storage buckets create "gs://$STAGING_BUCKET" \
    --project=$PROJECT_ID --location=$REGION --uniform-bucket-level-access
```

**Step 3 — Provision the IMS + data store + schema PATCH.**

Files to edit before running:
- `create_ds.py` — set `PROJECT_ID`, `PROJECT_NUMBER`, and `IMS_MAPPINGS`
  (external identity → user email) per your org.

```bash
python3 create_ds.py
```

**Step 4 — Build + push the container image** (see `DEPLOYMENT_GUIDE.md §4`):

```bash
gcloud artifacts repositories create gep-custom-connectors \
    --repository-format=docker --location=$REGION
gcloud builds submit \
    --tag "$REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest" \
    --timeout=15m .
```

Or `./deploy.sh` — runs pipeline-yaml validation + build + push + terraform
in one shot.

**Step 5 — Provision Cloud Run Job + Scheduler + IAM via Terraform.**

Files to edit before running:
- `pipelines/test_snooguts_mock.yaml` — set `project_id`, `data_store_id`,
  and `gcs_bucket` if you changed them from the defaults in Step 1–3.

```bash
export IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/gep-custom-connectors/custom-connectors:latest"

terraform -chdir=terraform/jobs init -reconfigure
terraform -chdir=terraform/jobs apply -auto-approve \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="image_uri=$IMAGE_URI"
```

**Step 6 — Trigger a run + verify.**

```bash
gcloud run jobs execute snooguts-mock-test-sync --region=$REGION --wait

# Fish the LRO id out of the execution logs
EXEC=$(gcloud run jobs executions list --job=snooguts-mock-test-sync \
    --region=$REGION --limit=1 --format='value(name)')
LRO=$(gcloud logging read "resource.type=cloud_run_job AND labels.\"run.googleapis.com/execution_name\"=$EXEC" \
    --limit=200 --format="value(textPayload)" | grep -oE "import-documents-[0-9]+" | head -1)

python3 check_ops.py "$LRO"
```

**Step 7 — ACL-aware search.**

```bash
python3 search_docs_v2.py --user=lead.one@example.com
```

Anonymous returns only `idp_wide` docs (4 of them: `initiative-102`,
`commitment-502`, `launch-802`, `person-9001`). A private-user match
returns 7 documents. Made-up test emails only match `idp_wide` — real
IdP users are needed for IMS-resolved group matches to fire.

**Ongoing operation.** Cloud Scheduler triggers the job hourly. To promote
a new image: re-run `deploy.sh` (or `gcloud run jobs update` for a
one-line image swap — `DEPLOYMENT_GUIDE.md §5b`).

---

## 7. Common issues + where to look

| Symptom                                                                              | Where to look                                                        | Fix                                                                                                                     |
|--------------------------------------------------------------------------------------|----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Import LRO returns `failureCount>0` with `INCORRECT_JSON_FORMAT`                     | Data store's `contentConfig`                                          | Must be `NO_CONTENT`. `CONTENT_REQUIRED` rejects every doc. Recreate the data store. ([MOD §8.1](./MODIFICATIONS_LOG.md#81-problem)) |
| Import LRO returns `Request contains an invalid argument` on rows with a group principal | `create_ds.py` output — did the IMS bind succeed?                    | Recreate the data store with `identity_mapping_store=<full IMS resource name using PROJECT NUMBER>`. Binding is immutable. ([MOD §12.2](./MODIFICATIONS_LOG.md#122-new-data-store-snooguts-ds-v5-with-ims-binding)) |
| Import LRO returns `Invalid datetime <value>` on `customProperties.value`            | Schema PATCH — was it applied?                                       | Re-run `python3 create_ds.py`; the schema PATCH is idempotent. ([MOD §12.3](./MODIFICATIONS_LOG.md#123-explicit-schema-patch--sidesteps-invalid-datetime-auto-inference)) |
| Fetcher WARN `read denied on policy-tagged column(s)`                                | Data Catalog policy tag on the column + SA's roles                   | Either grant `roles/datacatalog.categoryFineGrainedReader` on the tag, or accept the column exclusion.                  |
| Transformer WARN `Dropping doc <id>`                                                 | Source row's `private`/`allowedUsers`/`allowedGroups`/`ownerEmail`   | The row is private but has no principals. Fix at the source — this is intentional fail-safe behavior.                    |
| Transformer WARN `No transformer branch for entityType=<x>`                          | Fetcher discovered a table whose entityType isn't handled            | Either add a handler in `src/bigquery/transformers.py` OR filter the table out via `tables:` / `table_pattern:`.        |
| Cloud Run Job exits non-zero at start                                                | Cloud Run logs — service-account roles                               | The runtime SA usually needs `bigquery.dataViewer`+`bigquery.jobUser`+`discoveryengine.editor`. Terraform provisions all three. |
| Search returns 0 docs for a user you expect to match                                 | `snooguts-ds-v5` IMS mappings + Discovery Engine ACL semantics       | Search returns 0 for made-up test users. Real Google Workspace / Cloud Identity users are needed. IMS mappings must be loaded (see `create_ds.py::load_ims_mappings`). |
| `GetDocument` / `ListDocuments` returns 400 / empty                                  | This is by design on ACL-enabled data stores                         | Use `search:search` with `userInfo.userId` (or `search_docs_v2.py`) instead.                                            |

For deeper post-mortems of every production-visible bug + fix in this
repo's history, see [`MODIFICATIONS_LOG.md`](./MODIFICATIONS_LOG.md).

---

## 8. Sibling repo: Postgres/AWS variant

Same core engine, different source + different runtime host. GitHub:
[`lucaslm-stack/CC_GeminiEnterprise_MissionControl_Postgress`](https://github.com/lucaslm-stack/CC_GeminiEnterprise_MissionControl_Postgress).

What's different:

|                                 | This repo (BigQuery / GCP)                             | Sibling repo (Postgres / AWS)                                        |
|---------------------------------|--------------------------------------------------------|----------------------------------------------------------------------|
| Runtime host                    | Cloud Run Job                                          | ECS Fargate                                                          |
| Scheduler                       | Cloud Scheduler                                        | EventBridge Scheduler                                                |
| Source                          | BigQuery                                               | AWS RDS Postgres                                                     |
| Fetcher                         | `src/bigquery/fetchers.py`                             | `src/postgres/fetchers.py`                                           |
| Auth to source                  | Service-account IAM (data viewer + job user)           | DSN from AWS Secrets Manager, OR RDS IAM auth token                  |
| Auth to Discovery Engine        | Runtime SA on the Cloud Run Job                        | GCP SA JSON, materialized from AWS Secrets Manager by `entrypoint.sh` |
| Column-level restriction        | Data Catalog policy tags (fine-grained denial)         | Column-level `GRANT SELECT (col_a, ...)` (per-column ACL)            |
| Row-level restriction           | Row Access Policies                                    | `CREATE POLICY … ON tbl` + `ENABLE ROW LEVEL SECURITY`               |
| Table discovery                 | `client.list_tables(dataset_ref)`                      | `information_schema.tables`                                          |
| GCS staging bucket (FULL sync)  | Provisioned + wired up                                 | Not implemented; add if you need FULL sync                           |
| Terraform                       | GCP (`terraform/jobs/`)                                | AWS (`terraform/aws/`)                                               |

**Shared:** `src/core/*` engine, transformer envelope (`src/bigquery/transformers.py`
mirrors `src/postgres/transformers.py`), ACL contract, IMS binding,
`snooguts-ds-v5` data store, schema PATCH, log signal names.

**Why both exist:** the connector's core engine is source-agnostic on
purpose. The Postgres/AWS variant proves that swapping the source is a
fetcher swap + auth-plumbing change — the Custom Connector protocol on the
GCP side is identical.
