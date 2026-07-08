# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional, List, Generator
from ...core.models import RawPayload, PipelineContext
from .pull_request import GitHubPullRequestFetcher
from .issue import GitHubIssueFetcher
from .commit import GitHubCommitFetcher
from .base import BaseGitHubFetcher

logger = logging.getLogger("connector.github.fetchers.discussion")

class GitHubDiscussionsFetcher(BaseGitHubFetcher):
    """Fetcher that unifies Commit, PR, and Issue syncing into a single generator stream of discussion payloads."""

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        # 1. Instantiate the specialized fetchers
        pr_fetcher = GitHubPullRequestFetcher(**self.init_kwargs)
        issue_fetcher = GitHubIssueFetcher(**self.init_kwargs)
        commit_fetcher = GitHubCommitFetcher(**self.init_kwargs)
        
        # 2. Yield all PR payloads
        logger.info("Starting PR discussion extraction...")
        for item in pr_fetcher.fetch(context):
            yield item
            
        # 3. Yield all Issue payloads
        logger.info("Starting Issue discussion extraction...")
        for item in issue_fetcher.fetch(context):
            yield item
            
        # 4. Yield all Commit payloads
        logger.info("Starting Commit discussion extraction...")
        for item in commit_fetcher.fetch(context):
            yield item
