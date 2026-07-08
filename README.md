# Gemini Enterprise Custom Connectors Engine

> **Declarative custom connector ingestion framework surfacing enterprise data into Gemini Enterprise search.**

This repository contains the production-grade, serverless-first **Google Cloud Custom Connector Ingestion & Live Sync Engine** for Vertex AI Search and Gemini Enterprise.

---

## 💡 Core Principle: "Crawl Raw, Transform Rich"

Our architecture enforces a strict separation of concerns to maximize throughput and security:
1. **Phase 1: Retrieval (Fetchers):** Connectors crawl external enterprise systems (GitHub, REST, SQL) and yield untouched native raw response payloads.
2. **Phase 2: Adaptation (Transformers):** Transformers extract search content and map employee access controls directly to Google Workspace corporate emails (`user@company.com`) inside verified **Pure ACL** reader lists.

---

## 🏗️ Project Structure

```text
gep-cc-piper/
├── cloudbuild.yaml               # OOTB declarative CI/CD pipeline definition
├── deploy.sh                        # Universal local GitOps deployment runner
├── main.py                          # Serverless ingestion entry point
├── pipelines/                       # Active declarative pipeline configs (YAML)
│   ├── live_github_discussions_pipeline.yaml
│   └── live_github_docs_unified_pipeline.yaml
├── src/
│   ├── core/                        # Core pipeline runner, loaders, & GCS uploaders
│   ├── github/                      # GitHub Enterprise (GHES) crawl & ACL mappers
│   └── mock/                        # Offline local verification mock components
├── terraform/                       # Infrastructure as Code (Terraform 1.10+)
│   ├── jobs/                        # Dynamic fileset pipeline discovery root
│   └── modules/ingestion-pipeline/  # Reusable Cloud Run Job & Scheduler module
└── docs/                            # Comprehensive Developer Wiki
```

---

## 🔌 Core Abstractions

The framework uses three plugin components to achieve its sync goals:

#### `BaseDocumentFetcher`
* **Goal:** To crawl external data sources safely without hardcoding schema.
* **Implementation:** Streams raw source payloads lazily one by one (`yield RawPayload(...)`) using Python generators, guaranteeing low memory footprint even across multi-gigabyte repositories.

#### `BaseDocumentTransformer`
* **Goal:** To convert raw payloads into search-ready Gemini Enterprise contracts.
* **Implementation:** Extracts searchable text bytes and resolves corporate employee identities into `acl_info.readers` so users only search what they have permission to see.

#### `GoogleCloudDiscoveryEngineDocumentUploader`
* **Goal:** To ingest standardized document batches into Gemini Enterprise datastores.
* **Implementation:** Streams batches over gRPC and mounts shared Cloud Storage volume storage (`gs://...-cache`) to persist incremental sync state via SQLite.

---

## 🚀 Quick Start & Deployment

### 1. Local Verification & Dry-Runs
Verify codebase integrity and dry-run pipelines locally:
```bash
# Run automated test suite:
uv run python -m unittest discover tests

# Dry-run incremental sync:
uv run python main.py pipelines/live_github_docs_unified_pipeline.yaml --incremental
```

### 2. Infrastructure Deployment (`deploy.sh`)
Deploy or update Cloud Run jobs and periodic Cloud Scheduler triggers locally:
```bash
./deploy.sh
```
* **What happens:** Validates your YAML syntax, builds and pushes your Docker container image to Artifact Registry, and executes Terraform HCL to reconcile Google Cloud infrastructure automatically.

### 3. Automated CI/CD (`cloudbuild.yaml`)
Whenever your team merges a reviewed Pull Request into `main`, Google Cloud Build executes [cloudbuild.yaml](cloudbuild.yaml) to reconcile production infrastructure.

---

## 📖 Explore the Developer Wiki

Ready to build your own connector? Dive into our comprehensive wiki in `/docs`:
* 🚀 **[First-Time Deployment Guide](docs/First-Time-Deployment-Guide.md)** *(Beginner walkthrough)*
* 🔄 **[Enabling Continuous Deployment (CI/CD)](docs/Enabling-Continuous-Deployment.md)** *(Cloud Build setup)*
* 🛠️ **[How-to: Build New Pipelines](docs/How-to-Build-New-Pipelines.md)** *(Custom fetcher tutorial)*
* 💡 **[Best Practices Guide](docs/Best-Practices.md)** *(Rate limits & secret handling)*
* 📐 **[Architecture Philosophy](docs/Architecture-Philosophy.md)** *(Design rationale)*
* 📖 **[Glossary of Terms](docs/Glossary.md)** *(Codebase vocabulary)*
