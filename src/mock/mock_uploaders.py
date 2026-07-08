# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import os
import json
import logging
from typing import Iterable, Dict, Any
from google.cloud import discoveryengine_v1 as discoveryengine
from ..core.base import BaseDocumentUploader
from ..core.models import PipelineContext

logger = logging.getLogger("connector.mock.uploader")

class LocalNDJSONDocumentUploader(BaseDocumentUploader):
    """Simulates a local Vertex AI Search datastore index using offline NDJSON transaction log files."""
    def __init__(self, output_dir: str = "out", filename: str = "rest_ingested_documents.jsonl"):
        self.output_dir = output_dir
        self.filename = filename

    def upload(self, items: Iterable[discoveryengine.Document], context: PipelineContext) -> Dict[str, Any]:
        os.makedirs(self.output_dir, exist_ok=True)
        out_filepath = os.path.join(self.output_dir, self.filename)
        
        recon_str = context.config.get("reconciliation_mode", "INCREMENTAL").upper()
        logger.info(f"Writing outputs to local offline NDJSON: {out_filepath} (Sync Mode: {recon_str})")
        
        existing_documents = {}
        
        # In INCREMENTAL mode, we load existing indices from file to simulate differential updates
        if recon_str == "INCREMENTAL" and os.path.exists(out_filepath):
            try:
                with open(out_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            doc_dict = json.loads(line)
                            doc_id = doc_dict.get("id")
                            if doc_id:
                                existing_documents[doc_id] = doc_dict
                logger.info(f"Loaded {len(existing_documents)} existing documents from local index for incremental merge.")
            except Exception as e:
                logger.warning(f"Could not load existing NDJSON index for incremental merge: {e}")

        count = 0
        new_payloads = []
        
        for item in items:
            if not isinstance(item, discoveryengine.Document):
                raise TypeError(f"LocalNDJSONDocumentUploader expects discoveryengine.Document items; received {type(item).__name__}")
                
            # Convert native document to dict with camelCase keys for JSON compatibility
            payload = discoveryengine.Document.to_dict(
                item,
                preserving_proto_field_name=False
            )
            new_payloads.append(payload)
            count += 1
            context.increment_metric("uploaded")
            
        # Differential Merge logic
        if recon_str == "INCREMENTAL":
            for payload in new_payloads:
                doc_id = payload.get("id")
                if doc_id:
                    existing_documents[doc_id] = payload
            write_items = list(existing_documents.values())
        else:
            write_items = new_payloads
            
        # Overwrite target index file with the current state
        with open(out_filepath, "w", encoding="utf-8") as f:
            for payload in write_items:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                
        logger.info(f"Local Ingestion completed. Wrote {len(write_items)} documents to {out_filepath} ({count} new/updated in this run).")
        
        return {
            "destination_format": "NDJSON",
            "local_file_path": os.path.abspath(out_filepath),
            "total_records_indexed": len(write_items),
            "records_synced_this_run": count,
            "reconciliation_mode": recon_str,
            "status": "success"
        }
