# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import unittest
import time
import shutil
import os
from src.github.cache import PipelineCache

class TestPipelineCache(unittest.TestCase):
    
    def setUp(self):
        self.cache_dir = "test_cache_dir"
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
        self.cache = PipelineCache(redis_host=None, cache_dir=self.cache_dir)
        
    def tearDown(self):
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)

    def test_cache_miss(self):
        val = self.cache.get("non-existent-key")
        self.assertIsNone(val)
        
    def test_cache_hit_and_store(self):
        self.cache.set("my-key", {"foo": "bar"}, expire_seconds=10)
        val = self.cache.get("my-key")
        self.assertEqual(val, {"foo": "bar"})
        
    def test_cache_expiration(self):
        # Set short expiration
        self.cache.set("short-key", "some-value", expire_seconds=1)
        
        # Verify immediately active
        self.assertEqual(self.cache.get("short-key"), "some-value")
        
        # Wait for expiration
        time.sleep(1.5)
        
        # Verify expired
        self.assertIsNone(self.cache.get("short-key"))

    def test_cache_clear(self):
        self.cache.set("key1", "val1")
        self.cache.set("key2", "val2")
        self.assertEqual(self.cache.get("key1"), "val1")
        
        self.cache.clear()
        self.assertIsNone(self.cache.get("key1"))
        self.assertIsNone(self.cache.get("key2"))

if __name__ == "__main__":
    unittest.main()
