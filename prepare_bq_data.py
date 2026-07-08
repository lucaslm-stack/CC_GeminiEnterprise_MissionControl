# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import json
import os

def split_mock_data(input_file):
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    tables = {
        "initiatives": [],
        "commitments": [],
        "launches": [],
        "person": [],
        "user": []
    }
    
    for item in data:
        entity_type = item.get("entityType")
        if entity_type == "initiative":
            tables["initiatives"].append(item)
        elif entity_type == "commitment":
            tables["commitments"].append(item)
        elif entity_type == "launch":
            tables["launches"].append(item)
        elif entity_type == "person":
            tables["person"].append(item)
        elif entity_type == "user":
            tables["user"].append(item)
            
    os.makedirs("bq_load", exist_ok=True)
    
    for table_name, rows in tables.items():
        if rows:
            with open(f"bq_load/{table_name}.jsonl", "w") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")
            print(f"Created bq_load/{table_name}.jsonl with {len(rows)} rows.")

if __name__ == "__main__":
    split_mock_data("mock_data.json")
