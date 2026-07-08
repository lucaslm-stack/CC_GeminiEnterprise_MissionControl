# Glossary of Terms

A quick-reference guide explaining common concepts and terms used in this project.

---

### Pure ACL (Access Control List)
A security setup where document permissions only use verified company email addresses (like `name@company.com`). Public usernames, unmapped IDs, or broad groups aren't allowed. Vertex AI Search uses this email list to ensure users only see documents they have permission to view.

### CDC (Change Data Capture)
A way to sync only the data that has changed since your last run. For example, our fetchers save the latest sync timestamp so the next run only asks the API for newly updated items.

### Git OID (Object Identifier)
The unique hash (like a SHA string) that Git assigns to a specific version of a file or commit. Because these hashes never change, we save them in our cache so we can skip downloading files that haven't been edited.

### Reconciliation Mode
Tells the uploader how to handle document changes in Vertex AI Search:
* **`INCREMENTAL`**: Adds new documents and updates edited ones right away. It does not delete missing documents.
* **`FULL`**: Completely refreshes your catalog. All documents are written to temporary cloud storage, and Discovery Engine swaps the dataset, automatically removing any old documents that no longer exist in your source repository.

### Fetcher
The first step in a sync pipeline. It connects to your data source (like GitHub or Confluence), crawls through the items, and passes raw data downstream.

### Transformer
The middle step of the pipeline. It cleans up the raw data, extracts text, formats document IDs, resolves user permissions into company emails, and prepares the final search document.

### Uploader
The final step of the pipeline. It collects the formatted documents and sends them to Google Cloud Discovery Engine right away or in batches.

### Pipeline Context
An object passed along while the pipeline runs. It keeps track of configuration settings, active run IDs, item counts, and errors.

### Deployment Runner (`deploy.sh`)
An automated helper script ([deploy.sh](../deploy.sh)) that checks your configuration files for errors, builds your project container image, and deploys cloud jobs for every pipeline file in the `pipelines/` folder.

---

## 🧭 Navigation
* 🏠 [Wiki Home](README.md)
* 📐 [Architecture Philosophy](Architecture-Philosophy.md)
* 💡 [Best Practices Guide](Best-Practices.md)
* 🛠️ [How-to: Build New Pipelines](How-to-Build-New-Pipelines.md)
