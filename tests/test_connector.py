# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import os
import sys
import unittest
import json
import shutil

from src.core.models import PipelineContext
from src.core.loader import build_pipeline_from_yaml

class TestGeminiCustomConnector(unittest.TestCase):
    
    def setUp(self):
        # Setup clean local outputs directory for test runs
        self.output_dir = "out_test"
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)
            
    def tearDown(self):
        # Cleanup local outputs after test runs
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)

    def test_01_dynamic_pipeline_resolution(self):
        """Verifies that the dynamic reflection loader instantiates and type-checks modules correctly."""
        pipeline, config = build_pipeline_from_yaml("tests/test_data/doc_rest_pipeline.yaml")
        
        self.assertEqual(pipeline.name, "Mock Legacy REST Document Sync")
        self.assertEqual(type(pipeline.fetcher).__name__, "MockRESTFetcher")
        self.assertEqual(len(pipeline.transformers), 1)
        self.assertEqual(type(pipeline.transformers[0]).__name__, "RESTDocumentTransformer")
        self.assertEqual(type(pipeline.uploader).__name__, "LocalNDJSONDocumentUploader")
 
    def test_02_end_to_end_full_and_incremental_sync_differential_merges(self):
        """Verifies lazy streaming execution and differential offline merging of documents with Pure ACLs."""
        pipeline, config = build_pipeline_from_yaml("tests/test_data/doc_rest_pipeline.yaml")
        
        # Overwrite output locations for test safety
        pipeline.uploader.output_dir = self.output_dir
        pipeline.uploader.filename = "test_ingested_documents.jsonl"
        out_file = os.path.join(self.output_dir, "test_ingested_documents.jsonl")

        # ----------------------------------------------------
        # STEP 1: RUN FULL SYNC (BASELINE)
        # ----------------------------------------------------
        config["reconciliation_mode"] = "FULL"
        context_full = PipelineContext(config=config)
        
        sync_result_full = pipeline.run(context_full)
        self.assertTrue(sync_result_full.success)
        self.assertEqual(sync_result_full.metrics["fetched"], 3)
        self.assertEqual(sync_result_full.metrics["transformed"], 3)
        self.assertEqual(sync_result_full.metrics["uploaded"], 3)
        
        # Check that the baseline index file was written
        self.assertTrue(os.path.exists(out_file))
        with open(out_file, "r", encoding="utf-8") as f:
            baseline_lines = [json.loads(line) for line in f if line.strip()]
            
        self.assertEqual(len(baseline_lines), 3)
        doc_001_baseline = next(d for d in baseline_lines if d["id"] == "doc-001")
        self.assertEqual(doc_001_baseline["structData"]["title"], "Cymbal Group Employee Remote Work Policy & Handbook")
        # Pure ACL check:
        self.assertEqual(len(doc_001_baseline["aclInfo"]["readers"]), 1)
        principals_baseline = doc_001_baseline["aclInfo"]["readers"][0]["principals"]
        self.assertIn({"userId": "admin@rrolando.altostrat.com"}, principals_baseline)
        self.assertIn({"userId": "test1@rrolando.altostrat.com"}, principals_baseline)
        self.assertIn({"userId": "test2@rrolando.altostrat.com"}, principals_baseline)
 
        # ----------------------------------------------------
        # STEP 2: RUN INCREMENTAL SYNC (DELTA MERGE)
        # ----------------------------------------------------
        config["reconciliation_mode"] = "INCREMENTAL"
        context_incr = PipelineContext(config=config)
        
        sync_result_incr = pipeline.run(context_incr)
        self.assertTrue(sync_result_incr.success)
        self.assertEqual(sync_result_incr.metrics["fetched"], 4) # 4 delta documents (doc-001, doc-004, doc-005, doc-006)
        self.assertEqual(sync_result_incr.metrics["transformed"], 4)
        self.assertEqual(sync_result_incr.metrics["uploaded"], 4)
 
        # Check that incremental results merged into the baseline file (upserts)
        with open(out_file, "r", encoding="utf-8") as f:
            merged_lines = [json.loads(line) for line in f if line.strip()]
            
        # Total should be 6: baseline doc-002, doc-003, updated doc-001, and new doc-004, doc-005, doc-006
        self.assertEqual(len(merged_lines), 6)
        
        # Validate updated doc-001
        doc_001_updated = next(d for d in merged_lines if d["id"] == "doc-001")
        self.assertEqual(doc_001_updated["structData"]["title"], "Cymbal Group Employee Remote Work Policy & Handbook (v2.0)")
        self.assertEqual(doc_001_updated["structData"]["version"], "1.1_rest")
        # Updated Pure ACLs:
        self.assertEqual(len(doc_001_updated["aclInfo"]["readers"]), 1)
        principals_updated = doc_001_updated["aclInfo"]["readers"][0]["principals"]
        self.assertIn({"userId": "admin@rrolando.altostrat.com"}, principals_updated)
        self.assertIn({"userId": "test1@rrolando.altostrat.com"}, principals_updated)
        self.assertIn({"userId": "test2@rrolando.altostrat.com"}, principals_updated)
        self.assertIn({"userId": "special-reviewer@rrolando.altostrat.com"}, principals_updated)
        
        # Validate new doc-004
        doc_004_new = next(d for d in merged_lines if d["id"] == "doc-004")
        self.assertEqual(doc_004_new["structData"]["title"], "Cymbal Group FY2027 Financial Strategy & M&A Pipeline")
        self.assertEqual(len(doc_004_new["aclInfo"]["readers"]), 1)
        principals_doc4 = doc_004_new["aclInfo"]["readers"][0]["principals"]
        self.assertIn({"userId": "admin@rrolando.altostrat.com"}, principals_doc4)

        # Validate new binary doc-006
        doc_006_new = next(d for d in merged_lines if d["id"] == "doc-006")
        self.assertEqual(doc_006_new["structData"]["title"], "Cymbal Group FY2026 Core Infrastructure Policy")
        self.assertEqual(doc_006_new["structData"]["mimeType"], "application/pdf")
        self.assertTrue(
            doc_006_new["content"]["rawBytes"].startswith("JVBERi0x")
        )
        self.assertEqual(len(doc_006_new["aclInfo"]["readers"]), 1)
        principals_doc6 = doc_006_new["aclInfo"]["readers"][0]["principals"]
        self.assertIn({"userId": "admin@rrolando.altostrat.com"}, principals_doc6)
        self.assertIn({"userId": "test1@rrolando.altostrat.com"}, principals_doc6)

    def test_03_transformer_is_public_flag(self):
        """Verifies that setting is_public=True on GitHubFileTransformer bypasses ACL queries and sets idp_wide=True."""
        from src.github.transformers import GitHubFileTransformer
        from src.core.models import RawPayload
        
        transformer = GitHubFileTransformer(
            app_id="1",
            installation_id="1",
            is_public=True
        )
        
        payload = RawPayload(data={
            "id": "MyOwner_MyRepo_path_to_doc_md",
            "path": "path/to/doc.md",
            "content": "Hello World",
            "repository": "MyOwner/MyRepo",
            "oid": "12345",
            "byte_size": 11
        })
        
        context = PipelineContext()
        doc = transformer.transform(payload, context)
        
        self.assertIsNotNone(doc)
        self.assertEqual(doc.id, "MyOwner_MyRepo_path_to_doc_md")
        self.assertTrue(doc.acl_info.readers[0].idp_wide)
        self.assertFalse(doc.acl_info.readers[0].principals)

    def test_04_gcp_uploader_audit_toggle(self):
        """Verifies that audit logging is toggleable and OFF by default."""
        from src.core.gcp_uploaders import GoogleCloudDiscoveryEngineDocumentUploader
        
        uploader = GoogleCloudDiscoveryEngineDocumentUploader(
            project_id="test-project",
            data_store_id="test-store",
            gcs_bucket="test-bucket"
        )
        
        context = PipelineContext()
        
        # 1. By default, it should be OFF
        if "ENABLE_AUDIT_LOGGING" in os.environ:
            del os.environ["ENABLE_AUDIT_LOGGING"]
            
        uploader._log_audit_records([], context)
        self.assertFalse(hasattr(context, "audit_file_path"))
        
        # 2. When set to true, it should be ON
        os.environ["ENABLE_AUDIT_LOGGING"] = "true"
        uploader._log_audit_records([], context)
        self.assertTrue(hasattr(context, "audit_file_path"))
        
        # Cleanup file created by the test
        if hasattr(context, "audit_file_path") and os.path.exists(context.audit_file_path):
            os.remove(context.audit_file_path)
 
if __name__ == "__main__":
    unittest.main()
