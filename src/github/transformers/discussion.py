# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional, Any
from google.cloud import discoveryengine_v1 as discoveryengine
from ...core.base import BaseDocumentTransformer
from ...core.models import RawPayload, PipelineContext
from .pull_request import GitHubPullRequestTransformer
from .commit import GitHubCommitTransformer
from .issue import GitHubIssueTransformer

logger = logging.getLogger("connector.github.transformers.discussion")

class GitHubDiscussionTransformer(BaseDocumentTransformer):
    """Unified transformer that routes commit, PR, and issue payloads to their respective transformers."""
    
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key_path: Optional[str] = None,
        private_key_secret_name: Optional[str] = None,
        base_url: str = "https://api.github.com",
        enterprise_slug: Optional[str] = None,
        identity_mapper: Optional[Any] = None,
        is_public: bool = False
    ):
        # Instantiate sub-transformers
        self.pr_transformer = GitHubPullRequestTransformer(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url,
            enterprise_slug=enterprise_slug,
            identity_mapper=identity_mapper,
            is_public=is_public
        )
        self.commit_transformer = GitHubCommitTransformer(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url,
            enterprise_slug=enterprise_slug,
            identity_mapper=identity_mapper,
            is_public=is_public
        )
        self.issue_transformer = GitHubIssueTransformer(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url,
            enterprise_slug=enterprise_slug,
            identity_mapper=identity_mapper,
            is_public=is_public
        )

    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        if not isinstance(data.data, dict):
            return None
            
        payload_type = data.data.get("type")
        if payload_type == "pull_request":
            doc = self.pr_transformer.transform(data, context)
            if doc:
                doc.struct_data["type"] = "discussion_pr"
            return doc
        elif payload_type == "issue":
            doc = self.issue_transformer.transform(data, context)
            if doc:
                doc.struct_data["type"] = "discussion_issue"
            return doc
        elif payload_type == "commit":
            doc = self.commit_transformer.transform(data, context)
            if doc:
                doc.struct_data["type"] = "discussion_commit"
            return doc
            
        logger.warning(f"GitHubDiscussionTransformer received payload with unknown type: {payload_type}")
        return None
