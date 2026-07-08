# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional, List, Generator
from ...core.models import RawPayload, PipelineContext
from .base import _resolve_target_repositories_and_branches, BaseGitHubFetcher

logger = logging.getLogger("connector.github.fetchers.pull_request")

class GitHubPullRequestFetcher(BaseGitHubFetcher):
    """Fetches Pull Requests recursively from GitHub Enterprise v3.18, utilizing timestamp-based incremental sync."""
    
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
        logger.info(f"Resolved {len(target_repos)} repositories for PR sync: {list(target_repos.keys())}")
        
        # Determine last sync timestamp for CDC
        recon_mode = context.config.get("reconciliation_mode", "INCREMENTAL").upper()

        query = """
        query GetPullRequests($owner: String!, $repo: String!, $cursor: String) {
          repository(owner: $owner, name: $repo) {
            pullRequests(first: 30, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                id
                number
                title
                body
                state
                createdAt
                updatedAt
                author {
                  login
                  ... on User {
                    email
                  }
                }
                comments(first: 50) {
                  nodes {
                    id
                    body
                    createdAt
                    author {
                      login
                    }
                  }
                }
                reviews(first: 50) {
                  nodes {
                    id
                    body
                    state
                    createdAt
                    author {
                      login
                    }
                  }
                }
              }
            }
          }
        }
        """

        for repo_full_name in target_repos.keys():
            owner, repo_name = repo_full_name.split("/", 1)
            self.owner = owner
            logger.info(f"[{self.owner}/{repo_name}] Starting Pull Request extraction...")
            
            last_sync_key = f"github_pr_last_sync:{self.owner}/{repo_name}"
            last_sync_time = None
            
            if recon_mode == "INCREMENTAL":
                last_sync_time = self.cache.get(last_sync_key)
                if last_sync_time:
                    logger.info(f"[{self.owner}/{repo_name}] Incremental Run: Only fetching Pull Requests updated since: {last_sync_time}")

            has_next_page = True
            cursor = None
            latest_updated_at = None
            
            while has_next_page:
                variables = {
                    "owner": self.owner,
                    "repo": repo_name,
                    "cursor": cursor
                }
                
                try:
                    data = self.client.execute_graphql(query, variables)
                except Exception as e:
                    logger.error(f"[{self.owner}/{repo_name}] Failed to query Pull Requests: {e}")
                    context.record_error("github_pr_fetcher", repo_name, e)
                    break
                    
                repository_node = data.get("repository")
                if not repository_node:
                    logger.warning(f"Repository {self.owner}/{repo_name} not found.")
                    break
                    
                pr_connection = repository_node.get("pullRequests", {})
                nodes = pr_connection.get("nodes", []) or []
                page_info = pr_connection.get("pageInfo", {})
                
                if not nodes:
                    break
                    
                # Track the most recent updated timestamp in this run to save as the next sync checkpoint
                if not latest_updated_at:
                    latest_updated_at = nodes[0]["updatedAt"]

                for pr in nodes:
                    updated_at = pr["updatedAt"]
                    
                    # CDC Check: Since results are sorted UPDATED_AT DESC, we can break early
                    # the moment we see a PR updated prior to our last_sync_time
                    if last_sync_time and updated_at <= last_sync_time:
                        logger.info(f"[{self.owner}/{repo_name}] Reached PR updated at {updated_at}, which is older than last sync. Stopping fetch.")
                        has_next_page = False
                        break
                        
                    payload = {
                        "type": "pull_request",
                        "id": f"{self.owner}/{repo_name}:pr:{pr['number']}",
                        "number": pr["number"],
                        "title": pr["title"],
                        "body": pr["body"] or "",
                        "state": pr["state"],
                        "created_at": pr["createdAt"],
                        "updated_at": updated_at,
                        "author": pr.get("author") or {},
                        "comments": pr.get("comments", {}).get("nodes", []) or [],
                        "reviews": pr.get("reviews", {}).get("nodes", []) or [],
                        "repository": f"{self.owner}/{repo_name}"
                    }
                    
                    yield RawPayload(data=payload)
                    
                cursor = page_info.get("endCursor")
                has_next_page = has_next_page and page_info.get("hasNextPage", False)
                
            # Update last sync checkpoint
            if latest_updated_at:
                self.cache.set(last_sync_key, latest_updated_at, expire_seconds=86400 * 365)
