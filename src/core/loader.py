# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Universal Configuration Loader & Dependency Injection Bootstrapper.

This module dynamically loads declarative YAML pipeline configurations (from local disk or GCS) 
and utilizes Python reflection to resolve, instantiate, and wire together connector plugin components 
(fetchers, transformers, uploaders) into an executable ConnectorPipeline.
"""
import os
import logging
import importlib
from typing import Dict, Any, List, Tuple
import yaml

from .pipeline import ConnectorPipeline
from .base import BaseDocumentFetcher, BaseDocumentTransformer, BaseDocumentUploader

logger = logging.getLogger("connector.loader")

def load_yaml_config(filepath: str) -> dict:
    """Loads YAML pipeline configurations dynamically from local files or GCS (gs://)."""
    if filepath.startswith("gs://"):
        logger.info(f"Attempting to dynamically load config from GCS: {filepath}")
        # Parse GCS URI: gs://bucket-name/path/to/object.yaml
        path_parts = filepath[5:].split("/", 1)
        if len(path_parts) < 2:
            raise ValueError(f"Invalid GCS config URI: {filepath}")
        bucket_name, object_name = path_parts
        
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            content = blob.download_as_text(encoding="utf-8")
            logger.info("GCS Configuration file loaded successfully.")
        except Exception as e:
            raise RuntimeError(f"Failed to load configuration from GCS bucket '{bucket_name}' / '{object_name}': {e}")
    else:
        logger.info(f"Loading local configuration: {filepath}")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Configuration file not found: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

    return yaml.safe_load(content)

def resolve_class(class_path: str) -> Any:
    """Dynamically imports a class string reference using Python reflection."""
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)

def instantiate_component(component_config: Dict[str, Any]) -> Any:
    """Resolves and instantiates a class with provided parameters."""
    class_path = component_config.get("class")
    if not class_path:
        raise ValueError("Component config is missing the 'class' attribute.")
    params = component_config.get("params", {}) or {}
    cls = resolve_class(class_path)
    return cls(**params)

def build_pipeline_from_yaml(filepath: str, project_id_override: str = None) -> Tuple[ConnectorPipeline, dict]:
    """Parses config, instantiates fetchers, transformers, uploaders, and builds the pipeline."""
    config_dict = load_yaml_config(filepath)
    if "pipeline" not in config_dict:
        raise ValueError("Invalid pipeline config: Root key 'pipeline' is missing.")
    
    p_config = config_dict["pipeline"]
    name = p_config.get("name", "Unnamed Ingestion Pipeline")
    
    # Verify pipeline properties exist
    if "fetcher" not in p_config or "uploader" not in p_config:
        raise ValueError("Pipeline configuration requires both a 'fetcher' and an 'uploader'.")
        
    fetcher = instantiate_component(p_config.get("fetcher"))
    
    transformers = []
    for t_config in p_config.get("transformers", []) or []:
        transformers.append(instantiate_component(t_config))
        
    uploader_config = p_config.get("uploader")
    if project_id_override and "params" in uploader_config:
        if uploader_config["params"] is None:
            uploader_config["params"] = {}
        uploader_config["params"]["project_id"] = project_id_override
        
    uploader = instantiate_component(uploader_config)
    
    # Validate module subclass constraints
    if not isinstance(fetcher, BaseDocumentFetcher):
        raise TypeError(f"Fetcher class {type(fetcher).__name__} must subclass BaseDocumentFetcher")
    for t in transformers:
        if not isinstance(t, BaseDocumentTransformer):
            raise TypeError(f"Transformer class {type(t).__name__} must subclass BaseDocumentTransformer")
    if not isinstance(uploader, BaseDocumentUploader):
        raise TypeError(f"Uploader class {type(uploader).__name__} must subclass BaseDocumentUploader")
        
    pipeline = ConnectorPipeline(
        name=name, 
        fetcher=fetcher, 
        transformers=transformers, 
        uploader=uploader
    )
    return pipeline, p_config
