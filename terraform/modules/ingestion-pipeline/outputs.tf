output "job_name" {
  value       = google_cloud_run_v2_job.run_job.name
  description = "The name of the Cloud Run Job."
}

output "job_sa_email" {
  value       = google_service_account.job_sa.email
  description = "The Service Account email used by the Cloud Run Job."
}

output "scheduler_job_name" {
  value       = google_cloud_scheduler_job.cron_trigger.name
  description = "The name of the Cloud Scheduler trigger job."
}
