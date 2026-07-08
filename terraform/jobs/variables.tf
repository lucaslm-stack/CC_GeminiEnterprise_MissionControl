variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target GCP region."
}

variable "image_uri" {
  type        = string
  description = "The fully qualified Artifact Registry container image URI."
}


