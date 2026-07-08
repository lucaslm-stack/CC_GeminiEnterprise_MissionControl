import subprocess
import requests
import json

def get_token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode("utf-8").strip()

def get_operations(project_id):
    token = get_token()
    url = f"https://discoveryengine.googleapis.com/v1/projects/{project_id}/locations/global/collections/default_collection/dataStores/snooguts-ds/operations"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id
    }
    response = requests.get(url, headers=headers)
    print(f"Status: {response.status_code}")
    data = response.json()
    operations = data.get("operations", [])
    print(f"Total operations found: {len(operations)}")
    for op in operations[:10]: # Check last 10
        print(f"--- Operation: {op.get('name')} ---")
        print(f"Done: {op.get('done')}")
        if "error" in op:
            print(f"Error: {json.dumps(op['error'], indent=2)}")
        if "metadata" in op:
            metadata = op["metadata"]
            # Look for specific import metadata
            print(f"Type: {metadata.get('@type')}")
            if "successCount" in metadata:
                print(f"Success: {metadata.get('successCount')}")
            if "failureCount" in metadata:
                print(f"Failure: {metadata.get('failureCount')}")

if __name__ == "__main__":
    get_operations("creativestudiotest-492015")
