variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target GCP region."
}

variable "job_name" {
  type        = string
  description = "The name of the Cloud Run Job."
}

variable "image_uri" {
  type        = string
  description = "The container image URI stored in Artifact Registry."
}

variable "env_vars" {
  type        = map(string)
  default     = {}
  description = "Environment variables to inject into the Cloud Run container."
}

variable "secret_accessor_ids" {
  type        = list(string)
  default     = []
  description = "Secret Manager Secret resource IDs to grant accessor permissions for."
}

variable "cron_schedule" {
  type        = string
  default     = "0 * * * *" # Hourly
  description = "Cron schedule expression for Cloud Scheduler trigger."
}

variable "datastore_project_id" {
  type        = string
  description = "The GCP Project ID where the Discovery Engine datastore resides."
}

variable "gcs_bucket_name" {
  type        = string
  default     = ""
  description = "The name of the GCS bucket for staging document ingestion."
}

variable "cache_bucket_name" {
  type        = string
  default     = ""
  description = "The name of the GCS bucket for SQLite cache replication. If empty, a dedicated bucket will be provisioned."
}

