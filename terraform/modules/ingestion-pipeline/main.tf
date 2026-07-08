# Service Account for the specific ingestion job
resource "google_service_account" "job_sa" {
  account_id   = substr("${var.job_name}-sa", 0, 30)
  display_name = "Service Account for Ingestion Job: ${var.job_name}"
}

# Dedicated cache storage bucket for SQLite persistence between runs
resource "google_storage_bucket" "cache_bucket" {
  count                       = var.cache_bucket_name == "" ? 1 : 0
  name                        = "${var.project_id}-${var.job_name}-cache"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

locals {
  actual_cache_bucket_name = var.cache_bucket_name != "" ? var.cache_bucket_name : google_storage_bucket.cache_bucket[0].name
}


# Dynamic Secret Access bindings
resource "google_secret_manager_secret_iam_member" "secret_access" {
  count     = length(var.secret_accessor_ids)
  secret_id = var.secret_accessor_ids[count.index]
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.job_sa.email}"
}

# Standardized Cloud Run Job definition
resource "google_cloud_run_v2_job" "run_job" {
  name                = var.job_name
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.job_sa.email
      containers {
        image = var.image_uri
        env {
          name  = "GCS_CACHE_MOUNT"
          value = "/mnt/gcs-cache"
        }

        # Inject environment variables dynamically from the map variable
        dynamic "env" {
          for_each = var.env_vars
          content {
            name  = env.key
            value = env.value
          }
        }

        volume_mounts {
          name       = "gcs-cache-volume"
          mount_path = "/mnt/gcs-cache"
        }
      }

      volumes {
        name = "gcs-cache-volume"
        gcs {
          bucket    = local.actual_cache_bucket_name
          read_only = false
        }
      }
    }
  }

  depends_on = [
    google_service_account.job_sa,
    google_secret_manager_secret_iam_member.secret_access
  ]
}

# Standardized Cloud Scheduler Cron Trigger
resource "google_cloud_scheduler_job" "cron_trigger" {
  name        = "${var.job_name}-trigger"
  schedule    = var.cron_schedule
  time_zone   = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.run_job.name}:run"
    
    oauth_token {
      service_account_email = google_service_account.job_sa.email
    }
  }

  depends_on = [
    google_cloud_run_v2_job.run_job,
    google_service_account.job_sa
  ]
}

# Grant Cloud Scheduler permission to execute the Cloud Run Job
resource "google_cloud_run_v2_job_iam_member" "run_invoker" {
  project  = google_cloud_run_v2_job.run_job.project
  location = google_cloud_run_v2_job.run_job.location
  name     = google_cloud_run_v2_job.run_job.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.job_sa.email}"
}

# Grant the Job Service Account permission to write logs to Cloud Logging
resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

# Grant the Job Service Account permission to import/edit Vertex AI Search / Discovery Engine documents
resource "google_project_iam_member" "discoveryengine_editor" {
  project = var.datastore_project_id
  role    = "roles/discoveryengine.editor"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

# Grant the Job Service Account permission to read from BigQuery
resource "google_project_iam_member" "bigquery_viewer" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

# Grant the Job Service Account permission to read/write to the GCS bucket
resource "google_storage_bucket_iam_member" "gcs_bucket_admin" {
  count  = var.gcs_bucket_name != "" ? 1 : 0
  bucket = var.gcs_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job_sa.email}"
}

# Grant SA storage.objectAdmin on the cache bucket
resource "google_storage_bucket_iam_member" "cache_bucket_admin" {
  bucket = local.actual_cache_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job_sa.email}"
}

