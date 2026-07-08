# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.
"""
Secure GitHub App API Client & Rate-Limit Handler.

This module handles GitHub App authentication by generating RS256 JWTs from private keys, exchanging 
them for short-lived installation access tokens, managing automatic token rotation, and executing 
resilient GraphQL/REST API queries with exponential backoff and rate-limit retry logic.
"""
import os
import time
import logging
import requests
import jwt
from typing import Dict, Any, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("connector.github.client")

class GitHubAppClient:
    """Handles secure GitHub App authentication, token rotation, and rate-limit aware GraphQL API calls."""
    
    def __init__(
        self, 
        app_id: str, 
        installation_id: str, 
        private_key_path: Optional[str] = None, 
        private_key_secret_name: Optional[str] = None,
        base_url: str = "https://api.github.com"
    ):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key_path = private_key_path
        self.private_key_secret_name = private_key_secret_name
        self.base_url = base_url
        
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._private_key: Optional[str] = None

    def _load_private_key(self) -> str:
        """Loads the GitHub App Private Key PEM string from local files, GSM, or environment variables."""
        if self._private_key:
            return self._private_key

        # Option A: Load from environment variable directly (highest priority for local dry-runs)
        env_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
        if env_key:
            logger.info("GitHub App Private Key loaded from environment variable GITHUB_APP_PRIVATE_KEY.")
            self._private_key = env_key.replace("\\n", "\n")
            return self._private_key

        # Option B: Load from mounted volume path
        if self.private_key_path and os.path.exists(self.private_key_path):
            logger.info(f"Loading GitHub App Private Key from mounted path: {self.private_key_path}")
            with open(self.private_key_path, "r", encoding="utf-8") as f:
                self._private_key = f.read()
            return self._private_key

        # Option C: Load from Google Secret Manager dynamically
        if self.private_key_secret_name:
            logger.info(f"Loading GitHub App Private Key from Secret Manager: {self.private_key_secret_name}")
            try:
                from google.cloud import secretmanager
                client = secretmanager.SecretManagerServiceClient()
                response = client.access_secret_version(name=self.private_key_secret_name)
                self._private_key = response.payload.data.decode("utf-8")
                return self._private_key
            except Exception as e:
                raise RuntimeError(f"Failed to access secret '{self.private_key_secret_name}' in Google Secret Manager: {e}")

        raise ValueError(
            "GitHub App Private Key must be supplied either via the GITHUB_APP_PRIVATE_KEY environment variable, "
            "a mounted 'private_key_path', or a Secret Manager secret 'private_key_secret_name'."
        )

    def _generate_jwt(self) -> str:
        """Generates a signed JWT token authorizing as the GitHub App (valid for 10 minutes)."""
        private_key_pem = self._load_private_key()
        now = int(time.time())
        payload = {
            "iat": now - 60,       # Issued 1 minute in past to handle clock skew
            "exp": now + 600,      # Expire in 10 minutes
            "iss": self.app_id
        }
        # Encode and sign JWT using RSA algorithm
        return jwt.encode(payload, private_key_pem, algorithm="RS256")

    def _refresh_installation_token(self):
        """Requests a new repository installation access token, valid for 1 hour."""
        jwt_token = self._generate_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json"
        }
        url = f"{self.base_url}/app/installations/{self.installation_id}/access_tokens"
        
        logger.info(f"Requesting new Installation Access Token for installation ID: {self.installation_id}")
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        
        token_data = response.json()
        self._token = token_data["token"]
        
        # Parse expiration string (e.g. 2026-06-04T20:00:00Z) to local float timestamp
        from datetime import datetime
        try:
            self._token_expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            self._token_expires_at = time.time() + 3500 # Default to 58 minutes buffer

    def get_auth_token(self) -> str:
        """Returns the active installation token, refreshing it if expired or expiring within 5 minutes."""
        if not self._token or time.time() > (self._token_expires_at - 300):
            self._refresh_installation_token()
        return self._token

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def execute_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes a GraphQL query against GitHub with rate-limiting checks and retries."""
        token = self.get_auth_token()
        headers = {
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json"
        }
        
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
            
        if "/api/v3" in self.base_url:
            url = self.base_url.replace("/api/v3", "/api/graphql")
        else:
            url = f"{self.base_url}/graphql"

        
        response = requests.post(url, json=payload, headers=headers)
        
        # Check for rate limiting headers
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_time = response.headers.get("X-RateLimit-Reset")
        
        if remaining is not None and int(remaining) < 10:
            logger.warning(f"GitHub API Rate Limit critical. Remaining: {remaining}.")
            if reset_time:
                sleep_duration = max(0.0, float(reset_time) - time.time()) + 1.0
                logger.warning(f"Auto-throttling pipeline. Sleeping for {sleep_duration:.2f} seconds until rate limit resets...")
                time.sleep(sleep_duration)

        # Handle rate-limiting status 403 or server errors
        if response.status_code == 403 or response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            sleep_time = int(retry_after) if retry_after else 10
            logger.warning(f"GitHub API rate limited (Status {response.status_code}). Backing off for {sleep_time} seconds...")
            time.sleep(sleep_time)
            response.raise_for_status()

        response.raise_for_status()
        
        result_json = response.json()
        if "errors" in result_json:
            errors = result_json["errors"]
            logger.error(f"GraphQL execution returned errors: {errors}")
            raise ValueError(f"GraphQL Query execution failed: {errors}")
            
        return result_json.get("data", {})



    def get_installation_repositories(self) -> list[str]:
        """Lists all repository full names (owner/repo) accessible to the app installation."""
        token = self.get_auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        repos = []
        url = f"{self.base_url}/installation/repositories"
        params = {"per_page": 100}
        
        while url:
            logger.info(f"Querying GitHub installation repositories: {url}")
            response = requests.get(url, headers=headers, params=params if "per_page" in url or "?" not in url else None)
            
            if response.status_code != 200:
                raise ValueError(f"Failed to list installation repositories: {response.status_code} - {response.text}")
                
            data = response.json()
            for repo in data.get("repositories", []):
                repos.append(repo["full_name"])
                
            # Handle Link header pagination
            url = response.links.get("next", {}).get("url")
        
        return repos

