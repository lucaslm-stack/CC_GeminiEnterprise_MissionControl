# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
GitHub Base Fetcher & Repository Target Resolver.

This module provides common utilities and abstract base classes for GitHub data fetching. It resolves 
target repository strings, default Git branch names via GraphQL queries, and handles connector 
pagination loops across GitHub GraphQL and REST API endpoints.
"""
import logging
import re
from typing import Optional, List, Dict
from ..client import GitHubAppClient

logger = logging.getLogger("connector.github.fetchers.base")

def _resolve_target_repositories_and_branches(
    client: GitHubAppClient, 
    repo: Optional[str], 
    repos: Optional[List[str]], 
    default_branch: str
) -> Dict[str, str]:
    """Resolves the target repositories and their corresponding branch names to sync."""
    if repo:
        if ":" in repo:
            r, b = repo.split(":", 1)
            return {r: b}
        return {repo: default_branch}
        
    if repos:
        logger.info(f"Scanning all repositories accessible to this installation matching patterns: {repos}...")
        all_repos = client.get_installation_repositories()
        matched = {}
        
        for p in repos:
            if ":" in p:
                repo_pat, branch_pat = p.split(":", 1)
            else:
                repo_pat, branch_pat = p, default_branch
                
            try:
                rx = re.compile(repo_pat)
            except re.error as e:
                logger.error(f"Invalid repository regex pattern '{repo_pat}': {e}")
                continue
                
            for r in all_repos:
                if rx.match(r):
                    # Precedence: first match in the patterns list wins
                    if r not in matched:
                        matched[r] = branch_pat
                        
        return matched
        
    raise ValueError("Either 'repo' or 'repos' must be specified in the fetcher configuration.")

def _resolve_default_branch_name(client: GitHubAppClient, owner: str, repo: str) -> Optional[str]:
    """Queries repository metadata to resolve the name of the default branch."""
    query = """
    query GetDefaultBranch($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        defaultBranchRef {
          name
        }
      }
    }
    """
    try:
        data = client.execute_graphql(query, {"owner": owner, "repo": repo})
        ref = data.get("repository", {}).get("defaultBranchRef")
        if ref:
            return ref.get("name")
    except Exception as e:
        logger.warning(f"Failed to query default branch for {owner}/{repo}: {e}")
    return None


from ...core.base import BaseDocumentFetcher
from ..cache import PipelineCache

class BaseGitHubFetcher(BaseDocumentFetcher):
    """Common base class for GitHub fetchers to eliminate app-client and cache initialization boilerplate."""
    
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        repo: Optional[str] = None,
        repos: Optional[List[str]] = None,
        base_url: str = "https://api.github.com",
        redis_host: Optional[str] = None,
        private_key_path: Optional[str] = None,
        private_key_secret_name: Optional[str] = None,
        **kwargs
    ):
        self.repo = repo
        self.repos = repos
        self.client = GitHubAppClient(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url
        )
        self.cache = PipelineCache(redis_host=redis_host)
        
        # Keep track of kwargs to instantiate sub-fetchers (e.g. in unified discussions fetcher)
        self.init_kwargs = {
            "app_id": app_id,
            "installation_id": installation_id,
            "repo": repo,
            "repos": repos,
            "base_url": base_url,
            "redis_host": redis_host,
            "private_key_path": private_key_path,
            "private_key_secret_name": private_key_secret_name,
            **kwargs
        }

