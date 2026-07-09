"""Provision the snooguts custom-connector data store, including an Identity
Mapping Store binding so `external_group:` ACL principals resolve at query
time.

Idempotent — skips resources that already exist.
"""

from google.api_core import exceptions as gcp_exceptions
from google.cloud import discoveryengine_v1 as discoveryengine

PROJECT_ID = "creativestudiotest-492015"
# The Discovery Engine API strictly validates IMS resource names by project
# NUMBER, not ID, when binding to a data store.
PROJECT_NUMBER = "605470904306"
LOCATION = "global"

# Data store — must match pipelines/test_snooguts_mock.yaml.
DATA_STORE_ID = "snooguts-ds-v5"
DATA_STORE_DISPLAY_NAME = "Snooguts Mock Data Store"

# Identity mapping store — bound at data-store creation time (immutable after).
IMS_ID = "snooguts-ims"

# Example external-group -> user mappings. These are what make the ACL
# principal `external_group:pillar-growth-leads` actually resolve to real
# users at query time. Keep in sync with mock_data.json.
IMS_MAPPINGS = [
    ("pillar-growth-leads", "lead.one@example.com"),
    ("pillar-growth-leads", "exec.one@example.com"),
]


def ims_resource_name() -> str:
    return f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/identityMappingStores/{IMS_ID}"


def data_store_resource_name() -> str:
    return (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/collections/default_collection/dataStores/{DATA_STORE_ID}"
    )


def create_ims() -> str:
    """Create the Identity Mapping Store if it doesn't already exist. Returns
    the resource name."""
    client = discoveryengine.IdentityMappingStoreServiceClient()
    # get_ tolerates project id OR number; use id for readability.
    id_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/identityMappingStores/{IMS_ID}"
    try:
        client.get_identity_mapping_store(request={"name": id_name})
        print(f"IMS {IMS_ID} already exists — reusing")
        return ims_resource_name()
    except gcp_exceptions.NotFound:
        pass

    parent = client.common_location_path(project=PROJECT_ID, location=LOCATION)
    req = discoveryengine.CreateIdentityMappingStoreRequest(
        parent=parent,
        identity_mapping_store=discoveryengine.IdentityMappingStore(),
        identity_mapping_store_id=IMS_ID,
    )
    created = client.create_identity_mapping_store(request=req)
    print(f"Created IMS: {created.name}")
    return created.name


def load_ims_mappings() -> None:
    """Inline-import identity mappings into the IMS. Kicks off an LRO."""
    client = discoveryengine.IdentityMappingStoreServiceClient()
    entries = [
        discoveryengine.IdentityMappingEntry(
            external_identity=external_id,
            user_id=user_id,
        )
        for external_id, user_id in IMS_MAPPINGS
    ]
    req = discoveryengine.ImportIdentityMappingsRequest(
        identity_mapping_store=ims_resource_name(),
        inline_source=discoveryengine.ImportIdentityMappingsRequest.InlineSource(
            identity_mapping_entries=entries,
        ),
    )
    op = client.import_identity_mappings(request=req)
    print(f"IMS import LRO started — waiting…")
    result = op.result(timeout=180)
    print(f"IMS mappings loaded ({len(entries)} entries)")


def create_data_store() -> str:
    """Create the data store, bound to the IMS. Returns the resource name."""
    client = discoveryengine.DataStoreServiceClient()
    ds_name = data_store_resource_name()
    try:
        client.get_data_store(request={"name": ds_name})
        print(f"Data store {DATA_STORE_ID} already exists — reusing")
        return ds_name
    except gcp_exceptions.NotFound:
        pass

    parent = client.collection_path(PROJECT_ID, LOCATION, "default_collection")
    ds = discoveryengine.DataStore(
        display_name=DATA_STORE_DISPLAY_NAME,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        # Custom-connector docs carry metadata via json_data only — no content
        # block. CONTENT_REQUIRED would reject every document with
        # INCORRECT_JSON_FORMAT.
        content_config=discoveryengine.DataStore.ContentConfig.NO_CONTENT,
        solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
        acl_enabled=True,
        # Bind the IMS at creation time. This is IMMUTABLE — changing it
        # requires deleting and recreating the data store.
        identity_mapping_store=ims_resource_name(),
    )
    req = discoveryengine.CreateDataStoreRequest(
        parent=parent,
        data_store=ds,
        data_store_id=DATA_STORE_ID,
    )
    op = client.create_data_store(request=req)
    result = op.result(timeout=180)
    print(f"Created data store: {result.name} (bound to {IMS_ID})")
    return result.name


def main() -> None:
    create_ims()
    load_ims_mappings()
    create_data_store()


if __name__ == "__main__":
    main()
