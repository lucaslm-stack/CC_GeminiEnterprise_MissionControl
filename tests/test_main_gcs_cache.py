# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import unittest
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch
from src.core.models import PipelineContext, SyncResult

class TestMainGcsCacheSync(unittest.TestCase):
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.gcs_mount_dir = os.path.join(self.temp_dir, "gcs_mount")
        self.local_cache_dir = os.path.join(self.temp_dir, "local_cache")
        os.makedirs(self.gcs_mount_dir, exist_ok=True)
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch("main.build_pipeline_from_yaml")
    @patch("main.sys.exit")
    def test_gcs_cache_sync_flow(self, mock_exit, mock_build_pipeline):
        # 1. Create a dummy cache database in the "GCS mount"
        dummy_gcs_db = os.path.join(self.gcs_mount_dir, "some_pipeline_cache.db")
        with open(dummy_gcs_db, "w") as f:
            f.write("DUMMY_DATABASE_CONTENT")
            
        # 2. Setup mock pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.name = "Test Sync Pipeline"
        
        # When pipeline runs, it should create the local database changes
        local_db = os.path.join(self.local_cache_dir, "cache.db")
        def mock_pipeline_run(context):
            os.makedirs(self.local_cache_dir, exist_ok=True)
            with open(local_db, "w") as f:
                f.write("UPDATED_DATABASE_CONTENT")
            return SyncResult(
                run_id="test-run-123",
                success=True,
                metrics={"fetched": 1},
                errors=[]
            )
        mock_pipeline.run.side_effect = mock_pipeline_run
        mock_build_pipeline.return_value = (mock_pipeline, {})

        # 3. Trigger main.py flow with mock environments
        from main import main
        
        env_overrides = {
            "GCS_CACHE_MOUNT": self.gcs_mount_dir,
            "CACHE_DIR": self.local_cache_dir,
            "PIPELINE_CONFIG": "pipelines/some_pipeline.yaml"
        }
        
        with patch.dict(os.environ, env_overrides):
            with patch("sys.argv", ["main.py", "pipelines/some_pipeline.yaml"]):
                main()
            
        # 4. Verify that:
        # A. Local cache was populated from GCS on startup
        self.assertTrue(os.path.exists(local_db))
        
        # B. GCS cache was updated with the new changes on shutdown
        with open(dummy_gcs_db, "r") as f:
            gcs_content = f.read()
        self.assertEqual(gcs_content, "UPDATED_DATABASE_CONTENT")

if __name__ == "__main__":
    unittest.main()
