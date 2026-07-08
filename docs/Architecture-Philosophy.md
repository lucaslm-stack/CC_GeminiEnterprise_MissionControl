# Architecture Philosophy

Our framework is built to run fast, secure, and reliable document syncs from your repositories into Vertex AI Search and Google Discovery Engine.

---

## 1. Pipelines Come First

In our design, configuration files drive the work while Python code provides reusable tools.
* A pipeline configuration YAML (like [live_github_docs_unified_pipeline.yaml](../pipelines/live_github_docs_unified_pipeline.yaml)) defines the whole job: where to fetch data from, how to clean it up, where to upload it, and when it should run.
* Keeping configuration separate from code lets you set up dozens of different sync jobs (like daily file crawls or hourly pull request reviews) without writing new Python code.

---

## 2. Streaming Data One Item at a Time

To keep memory usage low even on huge repositories, we stream data instead of loading everything at once.
* Fetchers pull items one at a time using Python generators (`yield`).
* Transformers clean up each item as soon as it arrives, and uploaders send finished documents in small batches (like 100 items at a time).
* If one file fails, the pipeline logs the issue and keeps going so the rest of your sync finishes smoothly.

---

## 3. Verified Company Permissions (Pure ACLs)

Securing document access is our top priority. We use company email addresses to ensure users only see what they are allowed to search.
* **Verified Emails Only:** Search permissions in Discovery Engine can only use valid company emails (`user@company.com`).
* **Safe Defaults:** Public GitHub usernames or external accounts are never allowed. If we can't map a username to a company email, that user is left off the document permissions list to keep your data safe.
* For tips on mapping user identities, see the [Best Practices Guide](Best-Practices.md).

---

## 4. Keeping Code Separate from Deployment Setup

To make building and launching containers easy:
* The `/src` folder only contains Python code.
* All deployment setup (Terraform files) lives in the root `/terraform` folder.
* Pipelines share a reusable Terraform setup ([terraform/modules/ingestion-pipeline](../terraform/modules/ingestion-pipeline)) that automatically creates service accounts, sets permissions, and schedules runs.
* Cloud storage keeps track of your deployment state so all your pipeline configurations stay automatically synced.

---

## 5. Cross-Project Enterprise Ingestion

In enterprise environments, departmental compute pipelines are frequently isolated from centralized corporate search hubs.
* **Decoupled Targets:** The compute project hosting your Cloud Run runners and Cloud Scheduler triggers (`var.project_id`) is intentionally independent from the Vertex AI Search / Discovery Engine datastore project (`uploader.params.project_id`).
* **Automated Cross-Binding:** Our infrastructure automation detects cross-project configurations and dynamically binds IAM roles (`roles/discoveryengine.editor`) across project boundaries, enabling secure multi-project search architectures.

---

## 🧭 Navigation
* 🏠 [Wiki Home](README.md)
* 📖 [Glossary of Terms](Glossary.md)
* 💡 [Best Practices Guide](Best-Practices.md)
* 🛠️ [How-to: Build New Pipelines](How-to-Build-New-Pipelines.md)
