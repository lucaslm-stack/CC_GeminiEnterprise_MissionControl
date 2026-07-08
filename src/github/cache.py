# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Incremental Sync State Tracker & Caching Manager.

This module manages persistent deduplication and incremental sync state (ETags, Git OIDs, timestamps). 
It supports high-throughput distributed caching via Redis (GCP Memorystore) with an automated, 
zero-config fallback to a local persistent SQLite database file.
"""
import os
import json
import hashlib
import logging
import time
import sqlite3
from typing import Any, Optional

logger = logging.getLogger("connector.github.cache")

class PipelineCache:
    """Caching manager supporting Redis (GCP Memorystore) with automated fallback to SQLite database."""
    
    def __init__(self, redis_host: Optional[str] = None, redis_port: int = 6379, cache_dir: Optional[str] = None):
        self.redis_host = redis_host or os.environ.get("REDIS_HOST")
        self.redis_port = redis_port
        self.cache_dir = cache_dir or os.environ.get("CACHE_DIR", "/tmp/connector_cache")
        self.redis_client = None
        self._local_memory_cache = {}
        
        if self.redis_host:
            try:
                import redis
                logger.info(f"Connecting to Redis Cache host: {self.redis_host}:{self.redis_port}")
                self.redis_client = redis.Redis(
                    host=self.redis_host, 
                    port=self.redis_port, 
                    decode_responses=True,
                    socket_connect_timeout=3.0
                )
                self.redis_client.ping()
                logger.info("Successfully connected to Redis Cache.")
            except (ImportError, Exception) as e:
                logger.warning(f"Could not connect to Redis: {e}. Falling back to local SQLite cache.")
                self.redis_client = None

        if not self.redis_client:
            logger.info("Initializing local SQLite-based cache.")
            os.makedirs(self.cache_dir, exist_ok=True)
            self.db_path = os.path.join(self.cache_dir, "cache.db")
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """)
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed to initialize SQLite cache: {e}")

    def _hash_key(self, key: str) -> str:
        """Helper to hash key to create safe filesystem identifiers (if needed)."""
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Resolves cache value by key. Returns None on cache miss or expiration."""
        now = time.time()
        
        if self.redis_client:
            try:
                val = self.redis_client.get(key)
                if val:
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Redis cache read error: {e}")
            return None

        # Check local in-memory cache first
        if key in self._local_memory_cache:
            val, expires_at = self._local_memory_cache[key]
            if now < expires_at:
                return val
            else:
                del self._local_memory_cache[key]

        # Check SQLite database
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    value_json, expires_at = row
                    if now < expires_at:
                        val = json.loads(value_json)
                        self._local_memory_cache[key] = (val, expires_at)
                        return val
                    else:
                        cursor.execute("DELETE FROM cache WHERE key = ?", (key,))
                        conn.commit()
        except Exception as e:
            logger.warning(f"SQLite cache read error: {e}")
            
        return None

    def set(self, key: str, value: Any, expire_seconds: int = 3600) -> None:
        """Sets cache key to value with expiration time."""
        now = time.time()
        expires_at = now + expire_seconds
        
        if self.redis_client:
            try:
                self.redis_client.setex(key, expire_seconds, json.dumps(value))
                return
            except Exception as e:
                logger.warning(f"Redis cache write error: {e}")

        # Store in-memory cache
        self._local_memory_cache[key] = (value, expires_at)

        # Store in SQLite db
        value_json = json.dumps(value, ensure_ascii=False)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                    (key, value_json, expires_at)
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"SQLite cache write error: {e}")
            
    def clear(self) -> None:
        """Clears local in-memory and SQLite cache database."""
        self._local_memory_cache.clear()
        if self.redis_client:
            try:
                self.redis_client.flushdb()
            except Exception as e:
                logger.warning(f"Redis flush error: {e}")
        else:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM cache")
                    conn.commit()
                # Vacuum to reclaim space
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("VACUUM")
            except Exception as e:
                logger.warning(f"SQLite cache clear error: {e}")
        logger.info("Local caches cleared successfully.")
