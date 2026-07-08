# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

# Commit mining identity mapper
import logging
from typing import Iterable, List, Optional, Any
from src.core.base import BaseIdentityMapper
from src.core.models import PipelineContext

logger = logging.getLogger("connector.github.identity.commit_mining")

class GitHubCommitMiningIdentityMapper(BaseIdentityMapper):
    """Resolves collaborator logins by mining recent commit histories for matches user login -> author email."""
    def __init__(self, client: Optional[Any] = None):
        self.client = client

    def set_client(self, client: Any):
        self.client = client

    def map_identities(self, logins: Iterable[str], repository: str, context: PipelineContext) -> List[str]:
        if not self.client:
            raise ValueError("GitHubAppClient must be set on GitHubCommitMiningIdentityMapper before mapping.")
            
        owner, repo_name = repository.split("/", 1)
        logins_set = set(logins)
        if not logins_set:
            return []

        resolved_emails = {}
        
        logger.info(f"[{repository}] Attempting to resolve corporate emails for collaborators {list(logins_set)} via commit history...")
        commit_query = """
        query GetRepoCommits($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(first: 50) {
                    nodes {
                      author {
                        email
                        user {
                          login
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        try:
            commit_data = self.client.execute_graphql(commit_query, {"owner": owner, "repo": repo_name})
            ref = commit_data.get("repository", {}).get("defaultBranchRef") or {}
            target = ref.get("target") or {}
            history = target.get("history") or {}
            nodes = history.get("nodes") or []
            
            for c in nodes:
                author = c.get("author") or {}
                user = author.get("user") or {}
                login = user.get("login")
                email = author.get("email")
                if login and login in logins_set and email and "@" in email:
                    resolved_emails[login] = email
        except Exception as commit_err:
            logger.warning(f"[{repository}] Failed to scan commit history for identity mapping: {commit_err}")
            
        # Return list of emails for all resolved logins in the requested order
        return [resolved_emails.get(login) for login in logins]
