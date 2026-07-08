# How-to: Build and Deploy New Sync Pipelines

This tutorial walks you through building, testing, and deploying a new custom data sync pipeline.

---

## Step 1: Create Your Pipeline Config File

Create a new YAML configuration file inside the `pipelines/` folder (like [pipelines/my_connector_pipeline.yaml](../pipelines/my_connector_pipeline.yaml)).

Your file should define three main sections:
1. **`pipeline`**: Names your sync job and tells it which fetcher, transformer, and uploader classes to use.
2. **`uploader.params`**: Sets your target Google Cloud project ID, Vertex AI Search datastore ID, and staging bucket.
3. **`deployment`**: Sets your Cloud Run job name, schedule (cron string), sync mode, and secret keys.

```yaml
pipeline:
  name: "My Connector Sync"
  type: "document"
  fetcher:
    class: "src.my_connector.fetchers.MyFetcher"
    params:
      base_url: "https://api.mycompany.internal"
  transformers:
    - class: "src.my_connector.transformers.MyTransformer"
  uploader:
    class: "src.core.gcp_uploaders.GoogleCloudDiscoveryEngineDocumentUploader"
    params:
      # Search Hub project ID (can be separate from active compute project for cross-project ingestion)
      project_id: "my-search-hub-project-id"
      data_store_id: "my-datastore-id"
      location: "global"
      branch_id: "default_branch"
      gcs_bucket: "my-gcs-staging-bucket"

deployment:
  job_name: "my-connector-sync"
  cron_schedule: "0 4 * * *" # Run daily at 4:00 AM UTC
  reconciliation_mode: "INCREMENTAL"
  cache_bucket_name: "my-compute-project-id-connector-cache" # Shared persistent GCS SQLite mount
  secret_accessor_ids:
    - "projects/my-gcp-project-id/secrets/my-connector-secret"
  env_vars:
    MY_SECRET_NAME: "projects/my-gcp-project-id/secrets/my-connector-secret/versions/latest"
```

---

## Step 2: Write Your Python Code

If your connector needs custom logic, add your classes inside `/src`:

### 2.1 Fetcher (`src/my_connector/fetchers.py`)
Inherit from `BaseDocumentFetcher` and write a `fetch` generator that yields raw data wraps:
```python
from typing import Generator
from src.core.base import BaseDocumentFetcher
from src.core.models import RawPayload, PipelineContext

class MyFetcher(BaseDocumentFetcher):
    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        # Connect to API and yield items...
        yield RawPayload(data={"id": "doc_1", "text": "Hello world", "repository": "owner/repo"})
```

### 2.2 Transformer (`src/my_connector/transformers.py`)
Inherit from `BaseDocumentTransformer` to clean up the raw data and build search documents:
```python
from typing import Optional
from google.cloud import discoveryengine_v1 as discoveryengine
from src.core.base import BaseDocumentTransformer
from src.core.models import RawPayload, PipelineContext

class MyTransformer(BaseDocumentTransformer):
    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        payload = data.data
        # Format text, check permissions, and return document...
        return discoveryengine.Document(
            id=payload["id"],
            content=discoveryengine.Document.Content(raw_bytes=payload["text"].encode("utf-8")),
            acl_info=discoveryengine.Document.AclInfo(readers=[])
        )
```

---

## Step 3: Run Automated Tests

Before deploying, run automated unit tests to ensure:
* Your generator streams items one by one without running out of memory.
* Document IDs only use valid letters, numbers, hyphens, and underscores.
* User permissions map correctly to valid employee email addresses.

Run your test suite locally:
```bash
# Run all tests:
uv run python -m unittest discover tests

# Or run connector tests specifically:
uv run python -m unittest tests/test_connector.py
```

---

## Step 4: Deploy Your Pipeline

Once your tests pass locally, you can deploy your job to Google Cloud:

### Option A: Deploy Locally (`deploy.sh`)
Run our helper script directly from your terminal:
```bash
./deploy.sh
```
* **What happens:** The script validates your YAML files, builds your container image, pushes it to Google Artifact Registry, and sets up your Cloud Run job and scheduler trigger automatically.

### Option B: Merge via Pull Request (`cloudbuild.yaml`)
Commit your new YAML configuration file to a feature branch and open a Pull Request against `main`:
```bash
git checkout -b feat/add-my-connector
git add pipelines/my_connector_pipeline.yaml
git commit -m "feat: add my custom connector pipeline"
git push origin feat/add-my-connector
```
* **What happens:** Once your Pull Request is reviewed and merged into `main`, Google Cloud Build automatically executes our deployment runner ([cloudbuild.yaml](../cloudbuild.yaml)) to provision your cloud resources.

### Deleting a Pipeline
To remove a sync job, either delete its YAML file from `pipelines/` or add `.disabled` to its file name (like `my_connector_pipeline.yaml.disabled`). The next deployment run will automatically remove the cloud job.

---

## 🧭 Navigation
* 🏠 [Wiki Home](README.md)
* 📐 [Architecture Philosophy](Architecture-Philosophy.md)
* 📖 [Glossary of Terms](Glossary.md)
* 💡 [Best Practices Guide](Best-Practices.md)
