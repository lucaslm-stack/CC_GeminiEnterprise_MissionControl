# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Google Cloud Discovery Engine Native Document Backend Uploader.

This module implements the native document sync layer for Google Cloud Discovery Engine Datastores. 
It handles inline gRPC batch importing, large dataset staging via Google Cloud Storage (GCS), 
long-running operation (LRO) polling, metrics telemetry, and generic ingestion security audit logging.
"""
import datetime
import functools
import json
import logging
import os
import socket
import tempfile
from typing import Any, Dict, Iterable, List, Optional

from google.cloud import discoveryengine_v1 as discoveryengine
from google.cloud import storage

from .base import BaseDocumentUploader
from .models import PipelineContext

logger = logging.getLogger("connector.gcp.uploader")


def _batched(iterable: Iterable[Any], n: int) -> Iterable[List[Any]]:
    """Helper generator to yield lists of up to n items from an iterable."""
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


class GoogleCloudDiscoveryEngineDocumentUploader(BaseDocumentUploader):
    """Syncs native discoveryengine.Document objects directly to a live Google Cloud Discovery Engine Datastore."""
    
    def __init__(self, project_id: str, data_store_id: str, location: str = "global", branch_id: str = "default_branch", gcs_bucket: Optional[str] = None):
        self.project_id = project_id
        self.data_store_id = data_store_id
        self.location = location
        self.branch_id = branch_id
        self.gcs_bucket = gcs_bucket

    @functools.cached_property
    def client(self) -> discoveryengine.DocumentServiceClient:
        return discoveryengine.DocumentServiceClient()

    @functools.cached_property
    def storage_client(self) -> storage.Client:
        return storage.Client()

    @property
    def branch_path(self) -> str:
        return self.client.branch_path(
            project=self.project_id,
            location=self.location,
            data_store=self.data_store_id,
            branch=self.branch_id
        )

    def _build_summary(self, destination_format: str, count: int, status: str = "success", **kwargs) -> Dict[str, Any]:
        summary = {
            "destination_format": destination_format,
            "project_id": self.project_id,
            "data_store_id": self.data_store_id,
            "location": self.location,
            "branch_id": self.branch_id,
            "total_records_indexed": count,
            "status": status
        }
        summary.update(kwargs)
        return summary

    def upload(self, items: Iterable[discoveryengine.Document], context: PipelineContext) -> Dict[str, Any]:
        recon_str = context.config.get("reconciliation_mode", "INCREMENTAL").upper()
        recon_mode = (
            discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL
            if recon_str == "FULL"
            else discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL
        )
        if recon_mode == discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL:
            if not self.gcs_bucket:
                raise ValueError(
                    "Reconciliation mode FULL is not supported with inline imports. "
                    "Please configure 'gcs_bucket' in the uploader parameters to enable GCS staging."
                )
            return self._upload_via_gcs(items, recon_mode, recon_str, context)
        else:
            # INCREMENTAL sync bypasses GCS staging and uploads inline directly
            return self._upload_inline(items, recon_mode, recon_str, context)

    def _execute_import_operation(self, request: discoveryengine.ImportDocumentsRequest, context: PipelineContext, count: int):
        operation = self.client.import_documents(request=request)
        logger.info(f"Upload operation started: {operation.operation.name}. Waiting for completion...")
        operation.result()
        logger.info("Upload operation finished.")
        context.increment_metric("uploaded", count)

    def _upload_via_gcs(
        self, 
        items: Iterable[discoveryengine.Document], 
        recon_mode: discoveryengine.ImportDocumentsRequest.ReconciliationMode,
        recon_str: str,
        context: PipelineContext
    ) -> Dict[str, Any]:
        with tempfile.TemporaryFile(mode='w+') as f:
            logger.info("Writing documents to temporary local buffer for GCS staging...")
            count = 0
            list_items = []
            for item in items:
                if not isinstance(item, discoveryengine.Document):
                    raise TypeError(f"GoogleCloudDiscoveryEngineDocumentUploader expects discoveryengine.Document items; received {type(item).__name__}")
                
                doc_dict = discoveryengine.Document.to_dict(
                    item,
                    use_integers_for_enums=False,
                    preserving_proto_field_name=False
                )
                f.write(json.dumps(doc_dict) + "\n")
                list_items.append(item)
                count += 1
                
            if list_items:
                self._log_audit_records(list_items, context)

            if count == 0:
                logger.info("No documents to upload.")
                return self._build_summary("DiscoveryEngineAPI_GCS", 0)

            run_id = context.run_id
            gcs_blob_name = f"stage/{run_id}.jsonl"
            logger.info(f"Uploading {count} documents to gs://{self.gcs_bucket}/{gcs_blob_name}...")
            
            bucket = self.storage_client.bucket(self.gcs_bucket)
            blob = bucket.blob(gcs_blob_name)
            f.seek(0)
            blob.upload_from_file(f)
            
            gcs_uri = f"gs://{self.gcs_bucket}/{gcs_blob_name}"
            logger.info(f"Uploaded to {gcs_uri}. Triggering Discovery Engine import...")

            gcs_source = discoveryengine.GcsSource(
                input_uris=[gcs_uri],
                data_schema="document"
            )
            request = discoveryengine.ImportDocumentsRequest(
                parent=self.branch_path,
                gcs_source=gcs_source,
                reconciliation_mode=recon_mode
            )

            try:
                self._execute_import_operation(request, context, count)
            except Exception as e:
                logger.error(f"Failed to import documents from GCS: {e}")
                context.record_error("discovery_engine_client", f"gcs_import_{run_id}", e)
                raise
            finally:
                try:
                    logger.info(f"Cleaning up staged GCS object: {gcs_uri}")
                    blob.delete()
                except Exception as cleanup_err:
                    logger.warning(f"Failed to delete GCS object {gcs_uri}: {cleanup_err}")

        return self._build_summary(
            "DiscoveryEngineAPI_GCS",
            count,
            reconciliation_mode=recon_str
        )

    def _upload_inline(
        self, 
        items: Iterable[discoveryengine.Document], 
        recon_mode: discoveryengine.ImportDocumentsRequest.ReconciliationMode,
        recon_str: str,
        context: PipelineContext
    ) -> Dict[str, Any]:
        logger.info(f"Preparing to upload to Discovery Engine (Inline). Parent: {self.branch_path} (Mode: {recon_str})")
        
        total_uploaded = 0
        total_batches = 0
        
        for batch in _batched(items, 100):
            for item in batch:
                if not isinstance(item, discoveryengine.Document):
                    raise TypeError(f"GoogleCloudDiscoveryEngineDocumentUploader expects discoveryengine.Document items; received {type(item).__name__}")
            
            if recon_mode == discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL and total_batches > 0:
                logger.warning(
                    "Multiple batches detected in FULL sync mode! "
                    "Subsequent inline imports in FULL mode will overwrite previous batches in this run. "
                    "For large datasets, staging via GCS is recommended."
                )
            self._upload_batch(batch, recon_mode, context)
            self._log_audit_records(batch, context)
            total_uploaded += len(batch)
            total_batches += 1

        logger.info(f"Ingestion completed. Sent {total_uploaded} documents across {total_batches} batches.")
        
        return self._build_summary(
            "DiscoveryEngineAPI_Inline",
            total_uploaded,
            batches_sent=total_batches,
            reconciliation_mode=recon_str
        )

    def _upload_batch(
        self, 
        batch: List[discoveryengine.Document], 
        recon_mode: discoveryengine.ImportDocumentsRequest.ReconciliationMode,
        context: PipelineContext
    ):
        logger.info(f"Uploading batch of {len(batch)} documents to Discovery Engine...")
        inline_source = discoveryengine.ImportDocumentsRequest.InlineSource(documents=batch)
        request = discoveryengine.ImportDocumentsRequest(
            parent=self.branch_path,
            inline_source=inline_source,
            reconciliation_mode=recon_mode
        )
        try:
            self._execute_import_operation(request, context, len(batch))
        except Exception as e:
            logger.error(f"Failed to import document batch: {e}")
            for doc in batch:
                context.record_error("discovery_engine_client", doc.id, e)
            raise

    def _log_audit_records(self, items: List[discoveryengine.Document], context: PipelineContext):
        """Writes a detailed audit log of all uploaded documents and their ACLs for security verification/debugging."""
        if os.environ.get("ENABLE_AUDIT_LOGGING", "").lower() not in ("true", "1", "yes"):
            return
            
        out_dir = os.path.join(os.getcwd(), "out")
        os.makedirs(out_dir, exist_ok=True)
        
        # Ensure the filename is stable across multiple batches of the same pipeline run
        if not hasattr(context, "audit_file_path"):
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            context.audit_file_path = os.path.join(out_dir, f"audit_{timestamp_str}_{context.run_id}.json")
            context._audit_records = []

            env_details = {
                "hostname": socket.gethostname(),
                "k_service": os.environ.get("K_SERVICE"),
                "k_revision": os.environ.get("K_REVISION"),
                "k_configuration": os.environ.get("K_CONFIGURATION")
            }
            env_details = {k: v for k, v in env_details.items() if v is not None}
            pipeline_name = context.config.get("pipeline_name", "Unknown Pipeline")
            run_timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            context._audit_metadata = {
                "run_id": context.run_id,
                "pipeline_name": pipeline_name,
                "timestamp": run_timestamp,
                "project_id": self.project_id,
                "data_store_id": self.data_store_id,
                "location": self.location,
                "branch_id": self.branch_id,
                "gcs_bucket": self.gcs_bucket,
                "reconciliation_mode": context.config.get("reconciliation_mode", "INCREMENTAL").upper(),
                "deployment_environment": env_details
            }
            
        try:
            for item in items:
                doc_dict = discoveryengine.Document.to_dict(
                    item,
                    use_integers_for_enums=False,
                    preserving_proto_field_name=False
                )
                
                # Extract document content in a human-readable format
                content_str = ""
                if item.content and item.content.raw_bytes:
                    try:
                        content_str = item.content.raw_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        content_str = "[Binary or undecodable content]"

                struct_data = doc_dict.get("structData") or {}
                context._audit_records.append({
                    "document_id": doc_dict.get("id"),
                    "acl_info": doc_dict.get("aclInfo"),
                    "struct_data": struct_data,
                    "content": content_str
                })

            audit_data = {
                "run_metadata": context._audit_metadata,
                "uploaded_documents": context._audit_records
            }
            with open(context.audit_file_path, "w") as f:
                json.dump(audit_data, f, indent=2)

            logger.info(f"Detailed ingestion audit log written to: {context.audit_file_path}")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def delete(self, doc_id: str, context: PipelineContext) -> None:
        """Deletes a document from the live Discovery Engine Datastore by its sanitized document ID."""
        name = self.client.document_path(
            project=self.project_id,
            location=self.location,
            data_store=self.data_store_id,
            branch=self.branch_id,
            document=doc_id
        )
        logger.info(f"Deleting document from Discovery Engine: {name}")
        try:
            self.client.delete_document(name=name)
            logger.info(f"Successfully deleted document '{doc_id}'.")
        except Exception as e:
            logger.error(f"Failed to delete document '{doc_id}': {e}")
            context.record_error("discovery_engine_delete", doc_id, e)

