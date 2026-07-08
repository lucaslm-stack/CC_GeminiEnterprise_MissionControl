terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # 1. Scan pipelines directory for active YAML configuration files
  discovered_files = fileset("${path.module}/../../pipelines", "*.{yaml,yml}")

  # 2. Decode YAML contents into native HCL map objects
  parsed_pipelines = {
    for f in local.discovered_files : f => yamldecode(file("${path.module}/../../pipelines/${f}"))
  }

  # 4. Construct clean job configuration map for module for_each iteration
  jobs = {
    for f, p in local.parsed_pipelines : f => {
      job_name             = try(p.deployment.job_name, "")
      cron_schedule        = try(p.deployment.cron_schedule, "0 * * * *")
      reconciliation_mode  = try(p.deployment.reconciliation_mode, "INCREMENTAL")
      cache_bucket_name    = try(p.deployment.cache_bucket_name, "")
      secret_accessor_ids  = try(p.deployment.secret_accessor_ids, [])
      
      # Automatically inject PIPELINE_CONFIG and RECONCILIATION_MODE into runtime env vars
      env_vars = merge(
        try(p.deployment.env_vars, {}),
        {
          PIPELINE_CONFIG     = "pipelines/${f}"
          RECONCILIATION_MODE = try(p.deployment.reconciliation_mode, "INCREMENTAL")
        }
      )
      
      datastore_project_id = try(p.pipeline.uploader.params.project_id, var.project_id)
      gcs_bucket_name      = try(p.pipeline.uploader.params.gcs_bucket, "")
    }
  }
}

# Provision ingestion infrastructure (Cloud Run Jobs, Scheduler triggers, Service Accounts, IAM) dynamically
module "ingestion_sync" {
  source   = "../modules/ingestion-pipeline"
  for_each = local.jobs

  project_id           = var.project_id
  region               = var.region
  image_uri            = var.image_uri
  
  job_name             = each.value.job_name
  cron_schedule        = each.value.cron_schedule
  secret_accessor_ids  = each.value.secret_accessor_ids
  env_vars             = each.value.env_vars
  datastore_project_id = each.value.datastore_project_id != "" ? each.value.datastore_project_id : var.project_id
  gcs_bucket_name      = each.value.gcs_bucket_name
  cache_bucket_name    = each.value.cache_bucket_name
}
