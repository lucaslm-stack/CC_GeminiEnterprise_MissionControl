# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional, Dict, Any
from google.cloud import discoveryengine_v1 as discoveryengine
from ...core.base import BaseDocumentTransformer
from ...core.models import RawPayload, PipelineContext
from .file import GitHubFileTransformer
from .utils import _sanitize_doc_id, _get_web_base_url, _extract_jira_tickets, _build_native_acl_info

logger = logging.getLogger("connector.github.transformers.commit")

class GitHubCommitTransformer(BaseDocumentTransformer):
    """Transforms raw Git Commit payloads to Discovery Engine Document format with mapped corporate ACLs."""
    
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key_path: Optional[str] = None,
        private_key_secret_name: Optional[str] = None,
        base_url: str = "https://api.github.com",
        enterprise_slug: Optional[str] = None,
        identity_mapper: Optional[Dict[str, Any]] = None,
        is_public: bool = False
    ):
        self.file_transformer = GitHubFileTransformer(
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
        payload = data.data
        doc_id = payload.get("id")
        sha = payload.get("sha")
        message = payload.get("message")
        repo_full_name = payload.get("repository")
        
        if not doc_id or not sha or not repo_full_name:
            return None
            
        owner, repo = repo_full_name.split("/", 1)
        
        # 1. Map Author identity
        author_node = payload.get("author") or {}
        author_user = author_node.get("user") or {}
        author_email = None
        if author_user:
            name_id = (author_user
                       .get("organization", {})
                       .get("samlIdentity", {})
                       .get("externalIdentity", {})
                       .get("samlIdentity", {})
                       .get("nameId"))
            author_email = name_id if name_id else author_node.get("email")
        if not author_email:
            author_email = author_node.get("email")
            
        # 2. Compile Commit text body
        content_lines = [
            f"COMMIT: {sha}",
            f"Author: {author_node.get('name', 'Unknown')} <{author_email if author_email else 'Unknown'}>",
            f"Date: {payload.get('committed_date', '')}",
            "",
            "--- Commit Message ---",
            message
        ]
        compiled_text = "\n".join(content_lines)
        
        web_base = _get_web_base_url(self.file_transformer.client.base_url)
        source_url = f"{web_base}/{repo_full_name}/commit/{sha}"

        struct_data = {
            "type": "commit",
            "sha": sha,
            "repository": repo_full_name,
            "committedDate": payload.get("committed_date", ""),
            "mimeType": "text/plain",
            "sourceUrl": source_url
        }
        
        struct_data["jiraTickets"] = _extract_jira_tickets(message)
        contributors = self.file_transformer._resolve_repo_contributors(owner, repo, context)
        if contributors:
            struct_data["contributors"] = contributors
        
        # 3. Resolve Pure ACLs
        if self.file_transformer.is_public:
            native_acl_info = discoveryengine.Document.AclInfo(
                readers=[discoveryengine.Document.AclInfo.AccessRestriction(idp_wide=True)]
            )
        else:
            principals = self.file_transformer._resolve_repo_collaborator_emails(owner, repo, context)
            native_acl_info = _build_native_acl_info(principals)
            
        doc_content = discoveryengine.Document.Content(
            mime_type="text/plain",
            raw_bytes=compiled_text.encode("utf-8")
        )
        
        return discoveryengine.Document(
            id=_sanitize_doc_id(doc_id),
            struct_data=struct_data,
            content=doc_content,
            acl_info=native_acl_info
        )
