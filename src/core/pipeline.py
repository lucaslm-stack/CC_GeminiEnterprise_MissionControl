# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Core Stream Execution Engine & Pipeline Runner.

This module implements the central ingestion stream orchestrator (ConnectorPipeline). It drives 
the lazy generator pipeline flow: fetching raw source payloads, passing them through the ordered 
sequence of document transformers, tracking execution metrics, and streaming standardized 
Discovery Engine Document batches to the target backend uploader.
"""
import json
import time
import logging
from typing import Dict, Any, Iterable, List, Generator
from datetime import datetime

from google.cloud import discoveryengine_v1 as discoveryengine
from .models import PipelineContext, SyncResult, RawPayload
from .base import BaseDocumentFetcher, BaseDocumentTransformer, BaseDocumentUploader

logger = logging.getLogger("connector.pipeline")

class ConnectorPipeline:
    """The central stream orchestrator of the document ingestion pipeline."""
    def __init__(self, name: str, fetcher: BaseDocumentFetcher, transformers: List[BaseDocumentTransformer], uploader: BaseDocumentUploader):
        self.name = name
        self.fetcher = fetcher
        self.transformers = transformers or []
        self.uploader = uploader

    def _stream_items(self, context: PipelineContext) -> Generator[discoveryengine.Document, None, None]:
        """Streams Native RawPayload collections through transformers lazily to yield discoveryengine.Documents."""
        fetcher_name = type(self.fetcher).__name__
        error_policy = context.config.get("error_handling", "skip_and_log")
        logger.info(f"[{self.name}] Starting lazy fetch from: {fetcher_name} (Error Policy: {error_policy})")
        
        # Initialize source breakdown dictionary in state
        context.state["source_breakdown"] = {}
        source_breakdown = context.state["source_breakdown"]
        
        try:
            raw_generator = self.fetcher.fetch(context)
        except Exception as e:
            logger.error(f"[{self.name}] Failed to initialize fetcher {fetcher_name}: {e}")
            context.record_error(fetcher_name, "fetcher_init", e)
            if error_policy == "fail_fast":
                raise e
            return
 
        for raw_payload in raw_generator:
            context.increment_metric("fetched")
            
            # Determine grouping key (default to 'default', or 'repository' if present)
            group_key = "default"
            item_id = "unknown"
            if isinstance(raw_payload.data, dict):
                group_key = raw_payload.data.get("repository") or raw_payload.data.get("source") or "default"
                item_id = raw_payload.data.get("id") or raw_payload.data.get("doc_id") or "unknown"
            elif hasattr(raw_payload.data, "repository"):
                group_key = getattr(raw_payload.data, "repository") or "default"
            elif hasattr(raw_payload.data, "id"):
                item_id = getattr(raw_payload.data, "id")
                
            if group_key not in source_breakdown:
                source_breakdown[group_key] = {
                    "fetched": 0,
                    "transformed": 0,
                    "failed": 0,
                    "uploaded": 0
                }
                
            source_breakdown[group_key]["fetched"] += 1
            
            if not isinstance(raw_payload, RawPayload):
                err = TypeError(f"Fetcher yielded {type(raw_payload).__name__} instead of RawPayload wrapper.")
                context.record_error(fetcher_name, "fetch_stream", err)
                source_breakdown[group_key]["failed"] += 1
                if error_policy == "fail_fast":
                    raise err
                continue
 
            try:
                current_data: Any = raw_payload
 
                for transformer in self.transformers:
                    transformer_name = type(transformer).__name__
                    current_data = transformer.transform(current_data, context)
                    
                    if current_data is None:
                        logger.info(f"Transformer {transformer_name} filtered/skipped document '{item_id}'.")
                        break
                
                if current_data is not None:
                    if not isinstance(current_data, discoveryengine.Document):
                        raise TypeError(f"Transformer chain yielded {type(current_data).__name__} instead of discoveryengine.Document.")
                    
                    context.increment_metric("transformed")
                    source_breakdown[group_key]["transformed"] += 1
                    source_breakdown[group_key]["uploaded"] += 1
                    yield current_data
                    
            except Exception as e:
                logger.warning(f"[{self.name}] Error transforming document '{item_id}': {e}")
                context.record_error("transformer_chain", item_id, e)
                source_breakdown[group_key]["failed"] += 1
                if error_policy == "fail_fast":
                    logger.error(f"[{self.name}] Critical failure under 'fail_fast'. Aborting pipeline run.")
                    raise e
                continue

    def run(self, context: PipelineContext) -> SyncResult:
        """Orchestrates the vertical slice stream ingestion run and prints telemetry logs."""
        start_time = datetime.utcnow().isoformat() + "Z"
        uploader_name = type(self.uploader).__name__
        logger.info(f"[{self.name}] Initializing execution run ID: {context.run_id}")
        logger.info(f"[{self.name}] Streaming documents to uploader: {uploader_name}")
        
        success = True
        error_policy = context.config.get("error_handling", "skip_and_log")
        
        try:
            document_stream = self._stream_items(context)
            upload_summary = self.uploader.upload(document_stream, context)
            context.state["upload_summary"] = upload_summary
        except Exception as e:
            logger.critical(f"[{self.name}] Pipeline execution crashed in Uploader {uploader_name}: {e}", exc_info=True)
            context.record_error(uploader_name, "pipeline_sync_crash", e)
            success = False
            if error_policy == "fail_fast":
                raise e
        finally:
            if success and context.metrics.get("failed", 0) > 0 and context.config.get("fail_on_any_error", False):
                success = False
                logger.error(f"[{self.name}] Finished with errors; failing execution run as 'fail_on_any_error' is True.")
                
        end_time = datetime.utcnow().isoformat() + "Z"
        
        # Generate a structured telemetry report
        result = SyncResult(
            run_id=context.run_id,
            success=success,
            metrics=dict(context.metrics),
            errors=context.errors,
            state=dict(context.state)
        )
        
        run_report = {
            "run_id": context.run_id,
            "pipeline_name": self.name,
            "start_time": start_time,
            "end_time": end_time,
            "success": success,
            "metrics": result.metrics,
            "error_count": len(result.errors),
            "source_breakdown": context.state.get("source_breakdown")
        }
        
        # Structured JSON output to stdout for Google Cloud Logging aggregation
        print(f"JSON_TELEMETRY_REPORT: {json.dumps(run_report)}")
        return result
