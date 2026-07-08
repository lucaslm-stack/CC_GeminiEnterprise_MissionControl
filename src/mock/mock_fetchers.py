# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import os
import json
import time
import logging
from typing import Generator
from ..core.base import BaseDocumentFetcher
from ..core.models import RawPayload, PipelineContext

logger = logging.getLogger("connector.mock.fetchers")

class MockRESTFetcher(BaseDocumentFetcher):
    """Simulates a legacy REST API fetcher, yielding completely raw JSON data structures."""
    def __init__(self, data_dir: str = "src/mock/mock_data", base_url: str = "https://legacy.internal/api/v1"):
        self.data_dir = data_dir
        self.base_url = base_url

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        logger.info(f"Connecting to legacy REST API: {self.base_url}")
        
        recon_mode = context.config.get("reconciliation_mode", "INCREMENTAL").upper()
        filename = "noisy_rest_documents.json"
        
        if recon_mode == "INCREMENTAL":
            delta_file = os.path.join(self.data_dir, "noisy_rest_documents_delta.json")
            if os.path.exists(delta_file):
                filename = "noisy_rest_documents_delta.json"
                
        file_path = os.path.join(self.data_dir, filename)
        logger.info(f"Simulating {recon_mode} sync. Loading native raw response from: {file_path}")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Mock data file not found: {file_path}")
            
        with open(file_path, "r", encoding="utf-8") as f:
            raw_response_list = json.load(f)
            
        # Simulate minor network pagination delay
        time.sleep(0.1)
        
        for doc_item in raw_response_list:
            # We yield the legacy dictionary directly inside the RawPayload wrapper.
            # The fetcher has zero knowledge of document IDs, content text, or GEP formats.
            yield RawPayload(data=doc_item)
            
        logger.info("Fetcher operation completed successfully.")
