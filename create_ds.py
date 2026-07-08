import subprocess
import requests
import json

def get_token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode("utf-8").strip()

def create_data_store(project_id, data_store_id):
    token = get_token()
    url = f"https://discoveryengine.googleapis.com/v1/projects/{project_id}/locations/global/collections/default_collection/dataStores?dataStoreId={data_store_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id,
        "Content-Type": "application/json"
    }
    data = {
        "displayName": "Snooguts Mock Data Store",
        "industryVertical": "GENERIC",
        # Custom-connector docs carry metadata via json_data only (no content bytes),
        # so the store MUST be NO_CONTENT. CONTENT_REQUIRED rejects every doc with
        # INCORRECT_JSON_FORMAT because there is no content block.
        "contentConfig": "NO_CONTENT",
        "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
        "aclEnabled": True,
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))
    print(response.status_code)
    print(response.text)

if __name__ == "__main__":
    # Matches pipelines/test_snooguts_mock.yaml — v3 avoids clashing with the
    # existing CONTENT_REQUIRED v2 store, which cannot be edited to NO_CONTENT.
    create_data_store("creativestudiotest-492015", "snooguts-ds-v4")
