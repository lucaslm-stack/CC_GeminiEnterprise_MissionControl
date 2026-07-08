import subprocess
import requests
import json

def get_token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode("utf-8").strip()

def list_documents(project_id, data_store_id):
    token = get_token()
    # Using branch 0 which is the ID for default_branch in many operations
    url = f"https://discoveryengine.googleapis.com/v1/projects/{project_id}/locations/global/collections/default_collection/dataStores/{data_store_id}/branches/0/documents"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id
    }
    response = requests.get(url, headers=headers)
    print(f"Status: {response.status_code}")
    data = response.json()
    documents = data.get("documents", [])
    print(f"Total documents: {len(documents)}")
    for doc in documents:
        doc_id = doc.get("name", "").split("/")[-1]
        struct_data = doc.get("structData", {})
        title = struct_data.get("title", "No Title")
        print(f"- {doc_id}: {title}")

if __name__ == "__main__":
    list_documents("creativestudiotest-492015", "snooguts-ds-v2")
