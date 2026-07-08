# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
CLI Entry Point & Pipeline Orchestration Runner.

This script serves as the primary executable runner for the ingestion engine. It parses CLI parameters 
or default environment variables, initializes structured logging, handles GCS persistent cache database 
mount syncing, bootstraps the pipeline from YAML specifications via loader.py, and reports final telemetry.
"""
import sys
import logging
import json
from src.core.models import PipelineContext
from src.core.loader import build_pipeline_from_yaml

# Structured logging formatter to print clean logs to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("connector.runner")

def print_banner():
    banner = """
============================================================
           GEMINI ENTERPRISE INGESTION ENGINE               
============================================================
"""
    print(banner.strip())

def main():
    print_banner()
    
    import os
    
    # Check environment variable configuration, falling back to local default
    config_path = os.environ.get("PIPELINE_CONFIG", "pipelines/doc_rest_pipeline.yaml")
    
    # Parse positional configuration file argument (takes precedence over environment variable)
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if args:
        config_path = args[0]
        
    # Parse configuration overrides
    force_fail_fast = "--fail-fast" in sys.argv or os.environ.get("ERROR_HANDLING") == "fail_fast"
    force_skip_log = "--skip-and-log" in sys.argv or os.environ.get("ERROR_HANDLING") == "skip_and_log"
    force_full_sync = "--full" in sys.argv or "--full-sync" in sys.argv or os.environ.get("RECONCILIATION_MODE") == "FULL"
    force_incremental_sync = "--incremental" in sys.argv or "--incremental-sync" in sys.argv or os.environ.get("RECONCILIATION_MODE") == "INCREMENTAL"
    
    logger.info(f"Initializing runner. Target pipeline: {config_path}")
    
    try:
        pipeline, pipeline_config = build_pipeline_from_yaml(config_path)
    except Exception as e:
        logger.critical(f"Failed to load or build pipeline from '{config_path}': {e}", exc_info=True)
        sys.exit(1)
        
    # Determine error handling policy
    error_handling = pipeline_config.get("error_handling", "skip_and_log")
    if force_fail_fast:
        error_handling = "fail_fast"
    elif force_skip_log:
        error_handling = "skip_and_log"
        
    # Determine reconciliation mode
    reconciliation_mode = pipeline_config.get("reconciliation_mode", "INCREMENTAL")
    if force_full_sync:
        reconciliation_mode = "FULL"
        logger.info("Overriding reconciliation mode to 'FULL' via configuration overrides.")
    elif force_incremental_sync:
        reconciliation_mode = "INCREMENTAL"
        logger.info("Overriding reconciliation mode to 'INCREMENTAL' via configuration overrides.")
        
    run_config = {
        "error_handling": error_handling,
        "reconciliation_mode": reconciliation_mode,
        "config_source": config_path,
        "pipeline_name": pipeline.name
    }
    
    # GCS Volume Mount Cache synchronization
    gcs_mount_dir = os.environ.get("GCS_CACHE_MOUNT")
    local_cache_dir = os.environ.get("CACHE_DIR", "/tmp/connector_cache")
    gcs_db_path = None
    local_db_path = None
    
    if gcs_mount_dir:
        import shutil
        pipeline_slug = os.path.splitext(os.path.basename(config_path))[0]
        gcs_db_path = os.path.join(gcs_mount_dir, f"{pipeline_slug}_cache.db")
        local_db_path = os.path.join(local_cache_dir, "cache.db")
        
        logger.info(f"GCS_CACHE_MOUNT is set. Syncing cache from GCS mount: {gcs_db_path} to {local_db_path}...")
        
        if os.path.exists(gcs_db_path):
            try:
                os.makedirs(local_cache_dir, exist_ok=True)
                shutil.copy2(gcs_db_path, local_db_path)
                logger.info("Successfully copied cache from GCS mount to local temp cache.")
            except Exception as copy_err:
                logger.warning(f"Could not copy cache from GCS mount: {copy_err}. Starting with clean cache.")
        else:
            logger.info("No cache database found on GCS mount. A new one will be created.")

    context = PipelineContext(config=run_config)
    logger.info(f"Executing pipeline run: '{pipeline.name}'...")
    
    sync_result = None
    try:
        sync_result = pipeline.run(context)
    except Exception as e:
        logger.critical(f"Pipeline run crashed critically: {e}", exc_info=True)
    finally:
        # Sync cache back to GCS mount when done
        if gcs_mount_dir and local_db_path and os.path.exists(local_db_path):
            import shutil
            logger.info(f"Syncing updated cache back to GCS mount: {gcs_db_path}...")
            try:
                os.makedirs(gcs_mount_dir, exist_ok=True)
                shutil.copy2(local_db_path, gcs_db_path)
                logger.info("Successfully uploaded local cache to GCS mount.")
            except Exception as copy_err:
                logger.error(f"Failed to copy local cache to GCS mount: {copy_err}")
        
        if sync_result is None:
            sys.exit(1)
        
    print("\n" + "="*72)
    print("                   INGESTION SYNC COMPLETE REPORT")
    print("="*72)
    print(f"Pipeline Name : {pipeline.name}")
    print(f"Config Source : {config_path}")
    print(f"Run ID        : {sync_result.run_id}")
    print(f"Status        : {'SUCCESS' if sync_result.success else 'FAILED'}")
    print(f"Fetched       : {sync_result.metrics.get('fetched', 0)} raw items")
    print(f"Transformed   : {sync_result.metrics.get('transformed', 0)} items")
    print(f"Uploaded      : {sync_result.metrics.get('uploaded', 0)} items")
    print(f"Failed        : {sync_result.metrics.get('failed', 0)} items")
    print("="*72)
    
    # Print per-source repository breakdown table if present
    source_breakdown = sync_result.state.get("source_breakdown")
    if source_breakdown:
        print("\nSource Repository Ingestion Breakdown:")
        print(f" {'Repository/Source':<40} | {'Fetched':<8} | {'Transformed':<11} | {'Uploaded':<8} | {'Failed':<6}")
        print("-" * 83)
        for repo, metrics in sorted(source_breakdown.items()):
            display_repo = repo if len(repo) <= 40 else repo[:37] + "..."
            print(f" {display_repo:<40} | {metrics.get('fetched', 0):<8} | {metrics.get('transformed', 0):<11} | {metrics.get('uploaded', 0):<8} | {metrics.get('failed', 0):<6}")
        print("-" * 83)
    
    if sync_result.errors:
        print("\nExecution Warnings/Errors:")
        for idx, err in enumerate(sync_result.errors, 1):
            print(f" {idx}. [Component: {err['component']}] Item ID: '{err['item_id']}' -> {err['error_type']}: {err['message']}")
            
    if not sync_result.success:
        sys.exit(1)

if __name__ == "__main__":
    main()
