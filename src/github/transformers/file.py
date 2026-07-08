# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
GitHub Code & Document File Transformer.

This module implements the document transformer plugin for standard repository files. It adapts raw 
GitHub file payloads into Discovery Engine Documents, extracts Jira ticket references, queries Git 
commit history for contributor metadata, and resolves Pure ACL reader permissions.
"""
import os
import logging
from typing import Optional, List, Dict, Any
from google.cloud import discoveryengine_v1 as discoveryengine
from ...core.base import BaseDocumentTransformer
from ...core.models import RawPayload, PipelineContext
from ..client import GitHubAppClient
from ..cache import PipelineCache
from .utils import _sanitize_doc_id, _get_web_base_url, _extract_jira_tickets, _build_native_acl_info

logger = logging.getLogger("connector.github.transformers.file")

class GitHubFileTransformer(BaseDocumentTransformer):
    """Adapts raw GitHub repository file payloads to standardized Discovery Engine Document instances with Pure ACLs."""
    
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
        self.is_public = is_public
        self.client = GitHubAppClient(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url
        )
        self.cache = PipelineCache()
        self._cached_collaborator_principals: Dict[str, List[discoveryengine.Principal]] = {}
        self.enterprise_slug = enterprise_slug
        
        self.identity_mapper = None
        self.identity_mapper_config = identity_mapper
        if identity_mapper:
            from ...core.loader import instantiate_component
            self.identity_mapper = instantiate_component(identity_mapper)
            if hasattr(self.identity_mapper, "set_client"):
                self.identity_mapper.set_client(self.client)
        else:
            from ..identity import GitHubCommitMiningIdentityMapper
            self.identity_mapper = GitHubCommitMiningIdentityMapper(
                client=self.client
            )

    def _resolve_repo_collaborator_emails(self, owner: str, repo: str, context: PipelineContext) -> List[discoveryengine.Principal]:
        """Queries repository collaborators and maps their logins to Google Workspace email principals."""
        hot_cache_key = f"{owner}/{repo}"
        if hot_cache_key in self._cached_collaborator_principals:
            return self._cached_collaborator_principals[hot_cache_key]

        # Check persistent cache - vary cache key by identity mapper config hash
        import hashlib
        import json
        if self.identity_mapper_config:
            config_str = json.dumps(self.identity_mapper_config, sort_keys=True)
            config_hash = hashlib.sha256(config_str.encode("utf-8")).hexdigest()[:16]
        else:
            config_hash = "default_commit_mining"

        persistent_cache_key = f"github_sso_emails:{owner}/{repo}:{config_hash}"
        cached_emails = self.cache.get(persistent_cache_key)
        if cached_emails:
            logger.info(f"[{owner}/{repo}] Cache Hit for corporate SSO mappings. Bypassing GraphQL API.")
            principals = []
            for email in cached_emails:
                if email:
                    principal = discoveryengine.Principal()
                    principal.user_id = email
                    principals.append(principal)
            self._cached_collaborator_principals[hot_cache_key] = principals
            return principals

        logger.info(f"[{owner}/{repo}] Resolving repository collaborators SSO identity mappings...")
        
        # 1. Fetch collaborator logins from GitHub
        query = """
        query GetRepoCollaboratorLogins($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            collaborators(first: 100) {
              nodes {
                login
              }
            }
          }
        }
        """
        principals = []
        try:
            repository = f"{owner}/{repo}"
            data = self.client.execute_graphql(query, {"owner": owner, "repo": repo})
            nodes = data.get("repository", {}).get("collaborators", {}).get("nodes", []) or []
            collaborator_logins = [col["login"] for col in nodes]

            # 2. Invoke the pluggable identity mapper
            logger.info(f"[{repository}] Invoking pluggable identity mapper: {type(self.identity_mapper).__name__}")
            emails = self.identity_mapper.map_identities(collaborator_logins, repository, context)
            if emails is None:
                emails = []

            # Map resolved emails to Principal objects
            resolved_email_list = []
            for email in emails:
                if email:
                    principal = discoveryengine.Principal()
                    principal.user_id = email
                    principals.append(principal)
                    resolved_email_list.append(email)

            # Save to persistent cache with 2 hours TTL if we resolved emails successfully
            if resolved_email_list:
                self.cache.set(persistent_cache_key, resolved_email_list, expire_seconds=7200)

            logger.info(f"Successfully resolved {len(principals)} collaborator identities for {owner}/{repo}")

        except Exception as e:
            logger.warning(f"Failed to query repository collaborators SSO mappings: {e}. Defaulting to empty readers list.")
            context.record_error("github_transformer_auth", f"{owner}/{repo}_collaborators", e)
            raise e

        self._cached_collaborator_principals[hot_cache_key] = principals
        return principals

    def _resolve_repo_contributors(self, owner: str, repo: str, context: PipelineContext) -> List[str]:
        """Queries the commit history of the default branch and returns all unique contributor emails."""
        cache_key = f"github_repo_contributors:{owner}/{repo}"
        cached_contributors = self.cache.get(cache_key)
        if cached_contributors is not None:
            return cached_contributors

        logger.info(f"[{owner}/{repo}] Mining default branch commit history for contributor emails...")
        commit_query = """
        query GetRepoContributors($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(first: 100) {
                    nodes {
                      author {
                        email
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        contributors = []
        try:
            commit_data = self.client.execute_graphql(commit_query, {"owner": owner, "repo": repo})
            ref = commit_data.get("repository", {}).get("defaultBranchRef") or {}
            target = ref.get("target") or {}
            history = target.get("history") or {}
            nodes = history.get("nodes") or []
            
            unique_emails = set()
            for c in nodes:
                author = c.get("author") or {}
                email = author.get("email")
                if email and "@" in email:
                    unique_emails.add(email)
            
            contributors = sorted(list(unique_emails))
            self.cache.set(cache_key, contributors, expire_seconds=86400)
        except Exception as e:
            logger.warning(f"[{owner}/{repo}] Failed to mine commit history for contributors: {e}")
            
        return contributors

    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        if not isinstance(data.data, dict):
            raise TypeError(f"GitHubFileTransformer expects a dictionary payload; received {type(data.data).__name__}")

        payload = data.data
        doc_id = payload.get("id")
        path = payload.get("path")
        content = payload.get("content")
        repo_full_name = payload.get("repository") # e.g. "cymbal-group/gangplank-go"
        
        if not doc_id or not path or not repo_full_name:
            logger.warning("Received invalid raw payload; missing required fields: 'id', 'path', or 'repository'. Skipping.")
            return None

        owner, repo = repo_full_name.split("/", 1)
        
        # Build structural metadata
        web_base = _get_web_base_url(self.client.base_url)
        branch = payload.get("branch", "main")
        source_url = f"{web_base}/{repo_full_name}/blob/{branch}/{path}"

        struct_data = {
            "title": os.path.basename(path),
            "filePath": path,
            "gitOid": payload.get("oid", ""),
            "repository": repo_full_name,
            "byteSize": payload.get("byte_size", 0),
            "mimeType": "text/plain", # Default for raw code files
            "sourceUrl": source_url
        }
        
        struct_data["jiraTickets"] = _extract_jira_tickets(content)
        contributors = self._resolve_repo_contributors(owner, repo, context)
        if contributors:
            struct_data["contributors"] = contributors

        if self.is_public:
            native_acl_info = discoveryengine.Document.AclInfo(
                readers=[discoveryengine.Document.AclInfo.AccessRestriction(idp_wide=True)]
            )
        else:
            # Ingested files inherit repository-level collaborator permissions
            principals = self._resolve_repo_collaborator_emails(owner, repo, context)
            native_acl_info = _build_native_acl_info(principals)

        # Prepare Content block
        doc_content = None
        if content is not None:
            doc_content = discoveryengine.Document.Content(
                mime_type="text/plain",
                raw_bytes=content.encode("utf-8")
            )

        processed_doc = discoveryengine.Document(
            id=_sanitize_doc_id(doc_id), # Sanitize ID for Vertex AI Search compliance
            struct_data=struct_data,
            content=doc_content,
            acl_info=native_acl_info
        )

        return processed_doc
