# ONTAP API Catalog

Lab Builder keeps an offline ONTAP REST API compatibility catalog so upgrade planning does not depend on internet access or live vendor documentation. The catalog is generated from NetApp Swagger/OpenAPI reference pages and stored as a local SQLite database.

NetApp's public REST API reference is currently available through the ONTAP 9.18.1 doc site and lists older ONTAP 9 releases back to 9.9.1. ONTAP clusters also expose their API reference on-box at:

```text
https://<cluster-management-ip>/docs/api
```

## Files

```text
app/api_catalog/ontap.py
scripts/ontap-api-catalog
api_catalog/manifests/lab-builder-netapp.yml
api_catalog/ontap/specs/
api_catalog/ontap/ontap_api_catalog.sqlite3
```

- `app/api_catalog/ontap.py` contains the parser, SQLite builder, diff logic, and manifest validator.
- `scripts/ontap-api-catalog` is the operator CLI.
- `api_catalog/manifests/lab-builder-netapp.yml` maps Lab Builder capabilities to the ONTAP REST calls and field sets they depend on.
- `api_catalog/ontap/specs/` is the connected refresh cache for downloaded OpenAPI specs. Specs are stored as compressed `.json.gz` files by default.
- `api_catalog/ontap/ontap_api_catalog.sqlite3` is the offline runtime compatibility database.

## Connected Refresh

Run this only from a connected/admin workstation when you intentionally refresh vendor API data:

```bash
scripts/ontap-api-catalog fetch --versions "9.9.1 9.10.1 9.11.1 9.12.1 9.13.1 9.14.1 9.15.1 9.16.1 9.17.1 9.18.1"
scripts/ontap-api-catalog build
```

The fetch step downloads NetApp Swagger UI pages and extracts the embedded OpenAPI JSON. The build step generates the SQLite database used offline. Use `--uncompressed` on `fetch` only if you explicitly need plain `.json` files.

## Import From A Cluster Or Saved File

If a target release is not yet available through the public versioned NetApp reference, capture the Swagger UI HTML or OpenAPI JSON from an ONTAP cluster and import it:

```bash
scripts/ontap-api-catalog import-spec \
  --version 9.19.1 \
  --source /path/to/saved-ontap-docs-api.html \
  --source-url "https://<cluster-management-ip>/docs/api"

scripts/ontap-api-catalog build
scripts/ontap-api-catalog validate --versions "9.18.1 9.19.1"
```

As of the last catalog refresh, NetApp's public "what's new" page mentions ONTAP `9.19.1`, but the public Swagger reference fetched by this tool still published the same operation set as `9.18.1`. Import the on-box `9.19.1` reference before treating `9.19.1` as offline-supported.

## Offline Checks

Use the generated database before an ONTAP upgrade or during a disconnected maintenance window:

```bash
scripts/ontap-api-catalog summary
scripts/ontap-api-catalog validate --manifest api_catalog/manifests/lab-builder-netapp.yml --versions "9.14.1 9.18.1"
scripts/ontap-api-catalog diff --from 9.14.1 --to 9.18.1
```

`validate` returns exit code `0` when all required operations are present for the requested ONTAP versions. It returns exit code `2` when an operation or required fallback field set is missing.

## Support Window

The manifest currently treats ONTAP `9.14.1` through `9.18.1` as the default compatibility window for Lab Builder's NetApp workflows. Expand or contract `required_versions` in `api_catalog/manifests/lab-builder-netapp.yml` as your fleet policy changes.

When NetApp publishes a newer REST API reference, add the version to the fetch/build command, rebuild the catalog, and run:

```bash
scripts/ontap-api-catalog validate --versions "<current> <target>"
scripts/ontap-api-catalog diff --from <current> --to <target>
```

If the target version is not in the local database and the environment is offline, treat that as unsupported until a connected refresh or on-box import has been performed.
