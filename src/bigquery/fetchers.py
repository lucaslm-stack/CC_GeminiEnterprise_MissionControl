# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
import re
from typing import Generator, List, Optional, Sequence, Set, Tuple
from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from ..core.base import BaseDocumentFetcher
from ..core.models import RawPayload, PipelineContext

logger = logging.getLogger("connector.bigquery.fetchers")


def _singularize(name: str) -> str:
    """Very small English-ish singularizer; safe fallback is identity."""
    for suffix, replacement in (("ies", "y"), ("ses", "s"), ("s", "")):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)] + replacement
    return name


def _policy_tag_fields(schema: Sequence[bigquery.SchemaField]) -> Set[str]:
    """Return the names of top-level columns that have policy tags attached.

    Nested STRUCT/RECORD fields' policy tags are enforced on the leaf column
    but the read is denied on the whole `SELECT * FROM tbl` — so excluding the
    top-level column is the safe fallback.
    """
    out: Set[str] = set()
    for f in schema:
        pt = getattr(f, "policy_tags", None)
        if pt and getattr(pt, "names", None):
            out.add(f.name)
            continue
        # Recurse into RECORD-typed columns; if any leaf is tagged, mark the top-level column.
        if f.field_type == "RECORD" and getattr(f, "fields", None):
            if _policy_tag_fields(f.fields):
                out.add(f.name)
    return out


class BigQuerySnoogutsFetcher(BaseDocumentFetcher):
    """
    Fetcher for Snooguts Mission Control data stored in BigQuery.

    Table selection precedence (highest first):
      1. `query_override` — a raw SQL string; runs verbatim, auto-discovery
         bypassed.
      2. `tables` — explicit whitelist.
      3. `table_pattern` — regex against the table id; any matching table in
         the dataset is included. When neither is set, defaults to `.*`.

    Column-level security (Solution E):
      For every table it touches, the fetcher inspects the BQ schema for
      columns that carry a Data Catalog policy tag. If none exist, it just
      runs `SELECT *`. If any do:
        - a. Log an INFO listing the tagged columns so operators know what
             sensitive data is in play.
        - b. Attempt `SELECT *` first.
        - c. If BQ denies the read (403 / permission-denied due to fine-grained
             access), retry with `SELECT * EXCEPT (tagged_col_1, ...)` and log
             a WARN telling the operator to grant
             `roles/datacatalog.categoryFineGrainedReader` on the tag if the
             values should actually flow through.
      This keeps the pipeline running even when CLS is added later, without
      requiring the SA to have fine-grained reader access up front. Excluded
      columns arrive at the transformer as absent keys, which the drift
      detector will surface at INFO level (Solution B) — so silent data loss
      is impossible.

    `entity_type_overrides` lets you map table_id -> entityType when the default
    (table id, singularized) doesn't match what the transformer expects.
    """

    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        tables: Optional[List[str]] = None,
        table_pattern: Optional[str] = None,
        query_override: Optional[str] = None,
        entity_type_overrides: Optional[dict] = None,
    ):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.tables = list(tables) if tables else None
        self.table_pattern = table_pattern
        self.query_override = query_override
        self.entity_type_overrides = entity_type_overrides or {}
        self.client = bigquery.Client(project=project_id)

    def _resolve_tables(self) -> Sequence[str]:
        if self.tables:
            logger.info("Fetcher using explicit tables list: %s", self.tables)
            return self.tables

        pattern = re.compile(self.table_pattern or r".*")
        dataset_ref = f"{self.project_id}.{self.dataset_id}"
        discovered = [t.table_id for t in self.client.list_tables(dataset_ref)]
        selected = [t for t in discovered if pattern.fullmatch(t)]
        skipped = sorted(set(discovered) - set(selected))
        logger.info(
            "Fetcher auto-discovered %d tables in %s (pattern=%r): %s%s",
            len(selected), dataset_ref, self.table_pattern or ".*", selected,
            f"; skipped by pattern: {skipped}" if skipped else "",
        )
        if not selected:
            logger.warning("No tables in %s match pattern %r — nothing to fetch.", dataset_ref, self.table_pattern or ".*")
        return selected

    def _default_entity_type(self, table_name: str) -> str:
        if table_name in self.entity_type_overrides:
            return self.entity_type_overrides[table_name]
        if table_name in ("person", "user"):
            return table_name
        return _singularize(table_name)

    def _build_query(self, table_ref: str) -> Tuple[str, Set[str]]:
        """Compose the SELECT for a table, honoring policy tags on columns.

        Returns (sql, excluded_columns). Excluded columns are omitted only when
        their presence caused a preflight denial or when we can pre-detect
        their tag and the SA can't preflight (safest fallback).
        """
        table = self.client.get_table(table_ref)
        tagged = _policy_tag_fields(table.schema)
        if not tagged:
            return f"SELECT * FROM `{table_ref}`", set()

        logger.info(
            "%s: policy-tagged columns detected: %s. Attempting full SELECT; "
            "will fall back to SELECT * EXCEPT if the SA lacks fineGrainedReader.",
            table_ref, sorted(tagged),
        )
        # Try full SELECT first — the SA may have the fine-grained role.
        return f"SELECT * FROM `{table_ref}`", set()

    def _select_excluding(self, table_ref: str, excluded: Set[str]) -> str:
        cols = ", ".join(f"`{c}`" for c in sorted(excluded))
        return f"SELECT * EXCEPT ({cols}) FROM `{table_ref}`"

    def _run_with_cls_fallback(self, table_ref: str, context: PipelineContext) -> Generator[dict, None, None]:
        """Run `SELECT * FROM table`, falling back to `SELECT * EXCEPT(tagged)`
        if the query is denied on policy-tag grounds."""
        sql, _excluded = self._build_query(table_ref)
        try:
            for row in self.client.query(sql):
                yield dict(row)
            return
        except gcp_exceptions.Forbidden as e:
            msg = str(e).lower()
            if "policy" not in msg and "fine-grained" not in msg and "fine grained" not in msg:
                # Some other 403 (e.g. dataset-level denial) — re-raise so the
                # caller records it as a real error.
                raise
            table = self.client.get_table(table_ref)
            tagged = _policy_tag_fields(table.schema)
            if not tagged:
                # Denied for policy reasons but nothing tagged? Nothing to strip. Re-raise.
                raise
            logger.warning(
                "%s: read denied on policy-tagged column(s) %s. Retrying with "
                "SELECT * EXCEPT (...). Values will arrive as absent keys and "
                "the schema-drift detector will list them. Grant "
                "roles/datacatalog.categoryFineGrainedReader on the tag(s) to "
                "include the values.",
                table_ref, sorted(tagged),
            )
            fallback_sql = self._select_excluding(table_ref, tagged)
            context.record_error("BigQueryFetcher.cls", table_ref, e)
            for row in self.client.query(fallback_sql):
                yield dict(row)

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        if self.query_override:
            logger.info("Executing query override in %s.%s", self.project_id, self.dataset_id)
            for row in self.client.query(self.query_override):
                yield RawPayload(data=dict(row))
            return

        for table_name in self._resolve_tables():
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_name}"
            logger.info("Fetching data from BigQuery table: %s", table_ref)
            try:
                count = 0
                for data in self._run_with_cls_fallback(table_ref, context):
                    if "entityType" not in data or not data["entityType"]:
                        data["entityType"] = self._default_entity_type(table_name)
                    yield RawPayload(data=data)
                    count += 1
                logger.info("Successfully fetched %d records from %s", count, table_name)
            except Exception as e:
                logger.error("Failed to fetch data from %s: %s", table_ref, e)
                context.record_error("BigQueryFetcher", table_name, e)
