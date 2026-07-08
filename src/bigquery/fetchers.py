# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Generator, List, Optional
from google.cloud import bigquery
from ..core.base import BaseDocumentFetcher
from ..core.models import RawPayload, PipelineContext

logger = logging.getLogger("connector.bigquery.fetchers")

class BigQuerySnoogutsFetcher(BaseDocumentFetcher):
    """
    Fetcher for Snooguts Mission Control data stored in BigQuery.
    Queries tables for Initiatives, Commitments, Launches, and OrgData.
    """
    def __init__(
        self, 
        project_id: str, 
        dataset_id: str, 
        tables: Optional[List[str]] = None,
        query_override: Optional[str] = None
    ):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.tables = tables or ["initiatives", "commitments", "launches", "person", "user"]
        self.query_override = query_override
        self.client = bigquery.Client(project=project_id)

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        if self.query_override:
            logger.info(f"Executing query override in {self.project_id}.{self.dataset_id}")
            query_job = self.client.query(self.query_override)
            for row in query_job:
                yield RawPayload(data=dict(row))
            return

        for table_name in self.tables:
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_name}"
            logger.info(f"Fetching data from BigQuery table: {table_ref}")
            
            query = f"SELECT * FROM `{table_ref}`"
            try:
                query_job = self.client.query(query)
                count = 0
                for row in query_job:
                    # Convert BigQuery Row to dict
                    data = dict(row)
                    # Ensure entityType is present if not in table
                    if "entityType" not in data:
                        if table_name == "person":
                            data["entityType"] = "person"
                        elif table_name == "user":
                            data["entityType"] = "user"
                        else:
                            # Singularize table name if it ends with 's'
                            data["entityType"] = table_name[:-1] if table_name.endswith('s') else table_name
                    
                    yield RawPayload(data=data)
                    count += 1
                logger.info(f"Successfully fetched {count} records from {table_name}")
            except Exception as e:
                logger.error(f"Failed to fetch data from {table_ref}: {e}")
                context.record_error("BigQueryFetcher", table_name, e)
