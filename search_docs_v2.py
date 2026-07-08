import subprocess
import requests
import json

def get_token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode("utf-8").strip()

def search_documents(project_id, data_store_id, query):
    token = get_token()
    url = f"https://discoveryengine.googleapis.com/v1/projects/{project_id}/locations/global/collections/default_collection/dataStores/{data_store_id}/servingConfigs/default_search:search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id,
        "Content-Type": "application/json"
    }
    data = {
        "query": query,
        "pageSize": 10
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))
    print(f"Status: {response.status_code}")
    res_data = response.json()
    results = res_data.get("results", [])
    print(f"Total results: {len(results)}")
    for res in results:
        doc = res.get("document", {})
        doc_id = doc.get("name", "").split("/")[-1]
        struct_data = doc.get("structData", {})
        title = struct_data.get("title", "No Title")
        print(f"- {doc_id}: {title}")

if __name__ == "__main__":
    search_documents("creativestudiotest-492015", "snooguts-ds-v2", "Redwood")
