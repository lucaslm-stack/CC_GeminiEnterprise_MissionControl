# Best Practices Guide

Here are practical tips for building secure, reliable, and respectful data sync pipelines.

---

## 1. Respecting GitHub API Limits

Large GitHub instances can block your app if you ask for too much data too fast.
* **Skip Unchanged Files:** We cache file hashes. If a file hasn't changed since the last sync, we skip downloading it to save API calls.
* **Only Ask for New Edits:** Save the timestamp at the end of your run so the next sync only asks for items updated after that time.
* **Fetch in Reasonable Chunks:** When querying the API, ask for data in small pages (like 30 or 50 items at a time).

---

## 2. Managing Secret Keys Safely

Never hardcode passwords, private keys, or API tokens directly in your code or YAML files.
* **Keep Secrets Out of Code:** Always use Google Secret Manager.
* **Point to Latest Secrets:** In your pipeline config, point to the latest version of your secret (like `projects/123/secrets/my-key/versions/latest`).
* **Let Cloud Run Fetch Secrets:** Give your app service account permission to access the secret at runtime so keys never touch disk.

---

## 3. Securing Incoming Webhooks

If you build a web endpoint on Cloud Run to receive live update events from GitHub:
* **Check the Signature:** Always verify the `X-Hub-Signature-256` header on incoming requests.
* **Protect Against Timing Attacks:** Use standard `hmac.compare_digest()` to check the signature safely.
* **Reply Quickly:** Send back an `HTTP 202 Accepted` response right away. Do the heavy data processing in the background so GitHub doesn't time out waiting for your server.

---

## 4. Mapping User Identities

To match source usernames to company email addresses, you can choose from several helper tools in your pipeline config:
* **`UnifiedRepositoryIdentityMapper`**: Uses a static YAML file (like [acl.yaml](../src/github/identity/basic_mapping/acl.yaml)) to match repositories and groups to explicit employee emails.
* **`GitHubCommitMiningIdentityMapper`**: Checks recent Git commit history to match author usernames with their email addresses.
* **`DomainPassThroughIdentityMapper`**: Simply adds your company domain to usernames.
* **Safe Defaults:** If a username can't be mapped to a valid email, they are left off the document search permissions list to keep your data secure.

---

## 🧭 Navigation
* 🏠 [Wiki Home](README.md)
* 📐 [Architecture Philosophy](Architecture-Philosophy.md)
* 📖 [Glossary of Terms](Glossary.md)
* 🛠️ [How-to: Build New Pipelines](How-to-Build-New-Pipelines.md)
