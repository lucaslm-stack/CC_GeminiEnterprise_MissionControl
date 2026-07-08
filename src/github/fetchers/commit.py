# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional, List, Generator
from ...core.models import RawPayload, PipelineContext
from .base import _resolve_target_repositories_and_branches, _resolve_default_branch_name, BaseGitHubFetcher

logger = logging.getLogger("connector.github.fetchers.commit")

class GitHubCommitFetcher(BaseGitHubFetcher):
    """Fetches Git Commit History recursively from GitHub Enterprise v3.18, terminating on cached commit hashes (CDC)."""
    
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        repo: Optional[str] = None,
        repos: Optional[List[str]] = None,
        base_url: str = "https://api.github.com",
        redis_host: Optional[str] = None,
        private_key_path: Optional[str] = None,
        private_key_secret_name: Optional[str] = None
    ):
        self.owner = None
        super().__init__(
            app_id=app_id,
            installation_id=installation_id,
            repo=repo,
            repos=repos,
            base_url=base_url,
            redis_host=redis_host,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name
        )

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        target_repos = _resolve_target_repositories_and_branches(self.client, self.repo, self.repos, "HEAD")
        logger.info(f"Resolved {len(target_repos)} repositories for Commit sync: {list(target_repos.keys())}")
        
        recon_mode = context.config.get("reconciliation_mode", "INCREMENTAL").upper()

        query = """
        query GetCommits($owner: String!, $repo: String!, $expression: String!, $cursor: String) {
          repository(owner: $owner, name: $repo) {
            object(expression: $expression) {
              ... on Commit {
                history(first: 50, after: $cursor) {
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                  nodes {
                    oid
                    message
                    committedDate
                    author {
                      name
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
        """

        for repo_full_name, branch in target_repos.items():
            owner, repo_name = repo_full_name.split("/", 1)
            self.owner = owner
            
            if branch == "HEAD":
                resolved = _resolve_default_branch_name(self.client, owner, repo_name)
                if not resolved:
                    logger.warning(f"[{owner}/{repo_name}] Repository is empty or default branch could not be resolved. Skipping.")
                    continue
                branch = resolved
                
            logger.info(f"[{self.owner}/{repo_name}] Starting Git Commit History extraction on branch reference: {branch}")
            
            has_next_page = True
            cursor = None
            expression = branch
            
            while has_next_page:
                variables = {
                    "owner": self.owner,
                    "repo": repo_name,
                    "expression": expression,
                    "cursor": cursor
                }
                
                try:
                    data = self.client.execute_graphql(query, variables)
                except Exception as e:
                    logger.error(f"[{self.owner}/{repo_name}] Failed to query Commit History: {e}")
                    context.record_error("github_commit_fetcher", repo_name, e)
                    break
                    
                repository_node = data.get("repository")
                if not repository_node:
                    logger.warning(f"Repository {self.owner}/{repo_name} not found.")
                    break
                    
                object_node = repository_node.get("object")
                if not object_node:
                    logger.warning(f"[{self.owner}/{repo_name}] Could not resolve branch reference expression: {expression}")
                    break
                    
                history_connection = object_node.get("history", {})
                nodes = history_connection.get("nodes", []) or []
                page_info = history_connection.get("pageInfo", {})
                
                if not nodes:
                    break
                    
                for commit in nodes:
                    sha = commit["oid"]
                    cache_key = f"github_commit:{self.owner}/{repo_name}:{sha}"
                    
                    # CDC Check: If we see this commit hash in cache, we stop history traversal
                    if recon_mode == "INCREMENTAL" and self.cache.get(cache_key):
                        logger.info(f"[{self.owner}/{repo_name}] Commit {sha} is already cached. Reached sync checkpoint; terminating history search.")
                        has_next_page = False
                        break
                        
                    payload = {
                        "type": "commit",
                        "id": f"{self.owner}/{repo_name}:commit:{sha}",
                        "sha": sha,
                        "message": commit["message"],
                        "committed_date": commit["committedDate"],
                        "author": commit.get("author") or {},
                        "repository": f"{self.owner}/{repo_name}"
                    }
                    
                    # Cache the commit version hash
                    self.cache.set(cache_key, True, expire_seconds=86400 * 365) # Cache for 1 year
                    yield RawPayload(data=payload)
                    
                cursor = page_info.get("endCursor")
                has_next_page = has_next_page and page_info.get("hasNextPage", False)
