# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Abstract Plugin Interfaces & Connector Framework Contracts.

This module defines the foundational abstract base classes (BaseDocumentFetcher, BaseDocumentTransformer, 
BaseDocumentUploader, BaseIdentityMapper) that all datasource connectors and backend sync layers 
must subclass to guarantee contract compliance and interchangeability across the ingestion architecture.
"""
from abc import ABC, abstractmethod
from typing import Generator, Iterable, Dict, Any, Optional, List
from google.cloud import discoveryengine_v1 as discoveryengine
from .models import PipelineContext, RawPayload

class BaseDocumentFetcher(ABC):
    """Interface for custom connectors to retrieve legacy document payloads in native form."""
    @abstractmethod
    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        """Lazily yields raw, schema-less legacy data payloads from source APIs."""
        pass

class BaseDocumentTransformer(ABC):
    """Interface for adapting legacy raw payloads into standardized Discovery Engine Documents."""
    @abstractmethod
    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        """Maps native RawPayload to native discoveryengine.Document. 
        
        Extracts text, structural metadata, and parses Google Workspace emails 
        into acl_info.readers for Pure ACLs. Returns None to filter/skip.
        """
        pass

class BaseDocumentUploader(ABC):
    """Interface for syncing standardized Discovery Engine Documents to target backends."""
    @abstractmethod
    def upload(self, items: Iterable[discoveryengine.Document], context: PipelineContext) -> Dict[str, Any]:
        """Consumes discoveryengine.Document stream and returns sync statistics."""
        pass

class BaseIdentityMapper(ABC):
    """Interface for custom identity mapping logic linking source logins to corporate emails."""
    @abstractmethod
    def map_identities(self, logins: Iterable[str], repository: str, context: PipelineContext) -> List[str]:
        """Resolves the repository and its collaborator logins to a list of corporate reader emails."""
        pass
