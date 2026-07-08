# Gemini Custom Connectors Wiki

Welcome to the **Gemini Custom Connectors** developer wiki! This guide explains how our project is built, defines key terms, shares best practices, and walks you through setting up and running document syncs into Vertex AI Search.

---

## 📖 Wiki Table of Contents

### 1. [Architecture Philosophy](Architecture-Philosophy.md)
Learn about the core design ideas behind the project, like streaming data one item at a time to save memory, keeping configuration separate from code, and mapping user logins to verified company emails.

### 2. [Glossary of Terms](Glossary.md)
A handy cheat sheet explaining common terms used in the codebase, such as Pure ACLs, Change Data Capture, Git hashes, and sync modes.

### 3. [Best Practices Guide](Best-Practices.md)
Helpful tips on avoiding API rate limit blocks, verifying incoming webhooks, managing secret API keys safely, and handling user permissions.

### 4. [How-to: Build and Deploy New Pipelines](How-to-Build-New-Pipelines.md)
A step-by-step tutorial showing you how to create a new pipeline configuration file, write custom data fetchers or transformers, and deploy your changes.

### 5. [First-Time Deployment Guide](First-Time-Deployment-Guide.md)
A beginner-friendly walkthrough for setting up a brand new Google Cloud Project from scratch, enabling required APIs, storing secrets, and launching your first sync pipeline.

### 6. [Enabling Continuous Deployment (CI/CD)](Enabling-Continuous-Deployment.md)
A step-by-step guide for setting up automated remote builds and Terraform provisioning via Google Cloud Build, configuring dedicated runner service accounts, and ignoring non-code commits.
