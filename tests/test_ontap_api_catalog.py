import json

import yaml

from app.api_catalog.ontap import build_catalog, diff_versions, extract_swagger_spec_from_html, import_spec_file, validate_manifest


def _write_spec(path, *, version, include_new_field=False, include_snapshot=False):
    volume_parameters = [
        {"name": "name", "in": "query", "type": "string", "x-ntap-introduced": "9.1"},
        {"name": "size", "in": "query", "type": "integer", "x-ntap-introduced": "9.1"},
        {"name": "fields", "in": "query", "type": "array"},
    ]
    if include_new_field:
        volume_parameters.append({"name": "new_field", "in": "query", "type": "string", "x-ntap-introduced": "9.2"})
    paths = {
        "/storage/volumes": {
            "get": {
                "operationId": "volume_collection_get",
                "x-ntap-introduced": "9.1",
                "parameters": volume_parameters,
                "responses": {"200": {"schema": {"$ref": "#/definitions/volume_response"}}},
            }
        },
        "/storage/volumes/{uuid}": {
            "patch": {
                "operationId": "volume_patch",
                "x-ntap-introduced": "9.1",
                "parameters": [
                    {"name": "uuid", "in": "path", "required": True, "type": "string"},
                    {"name": "body", "in": "body", "schema": {"$ref": "#/definitions/volume_patch"}},
                ],
                "responses": {"202": {"description": "Accepted"}},
            }
        },
    }
    if include_snapshot:
        paths["/storage/snapshots"] = {
            "get": {
                "operationId": "snapshot_collection_get",
                "x-ntap-introduced": "9.2",
                "parameters": [{"name": "name", "in": "query", "type": "string"}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    spec = {
        "info": {"title": f"ONTAP {version} REST API"},
        "basePath": "/api",
        "paths": paths,
        "definitions": {"volume_response": {}, "volume_patch": {}},
    }
    path.write_text(json.dumps(spec), encoding="utf-8")


def test_extract_swagger_spec_from_netapp_style_html():
    html = '<script id="SwaggerYAML" type="application/json">{"paths":{"/cluster":{"get":{"responses":{}}}}}</script>'

    spec = extract_swagger_spec_from_html(html)

    assert "/cluster" in spec["paths"]


def test_import_spec_file_accepts_saved_swagger_html(tmp_path):
    html_path = tmp_path / "docs-api.html"
    spec_dir = tmp_path / "specs"
    html_path.write_text(
        '<script id="SwaggerYAML" type="application/json">{"info":{"title":"ONTAP"},"basePath":"/api","paths":{"/cluster":{"get":{"responses":{}}}}}</script>',
        encoding="utf-8",
    )

    result = import_spec_file(html_path, "9.19.1", spec_dir, source_url="https://cluster/docs/api")

    assert result["version"] == "9.19.1"
    assert result["paths"] == 1
    assert (spec_dir / "9.19.1.json.gz").is_file()


def test_build_catalog_and_validate_manifest_with_fallback_fields(tmp_path):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    _write_spec(spec_dir / "9.1.1.json", version="9.1.1")
    _write_spec(spec_dir / "9.2.1.json", version="9.2.1", include_new_field=True, include_snapshot=True)
    db_path = tmp_path / "catalog.sqlite3"
    manifest_path = tmp_path / "manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "application": "test-app",
                "product": "ontap",
                "required_versions": ["9.1.1", "9.2.1"],
                "capabilities": {
                    "volumes": {
                        "operations": [
                            {
                                "method": "GET",
                                "path": "/api/storage/volumes",
                                "field_sets": [["name", "new_field"], ["name", "size"]],
                            },
                            {"method": "PATCH", "path": "/api/storage/volumes/{volume_uuid}"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    summary = build_catalog(spec_dir, db_path)
    result = validate_manifest(manifest_path, db_path)

    assert summary["operations"] == 5
    assert result["ok"] is True
    assert result["versions"]["9.1.1"]["warnings"]
    assert result["versions"]["9.2.1"]["warnings"] == []


def test_validate_manifest_blocks_missing_operation(tmp_path):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    _write_spec(spec_dir / "9.1.1.json", version="9.1.1")
    db_path = tmp_path / "catalog.sqlite3"
    manifest_path = tmp_path / "manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "application": "test-app",
                "product": "ontap",
                "required_versions": ["9.1.1"],
                "capabilities": {
                    "snapshots": {
                        "operations": [
                            {"method": "GET", "path": "/api/storage/snapshots"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    build_catalog(spec_dir, db_path)
    result = validate_manifest(manifest_path, db_path)

    assert result["ok"] is False
    assert "GET /api/storage/snapshots" in result["versions"]["9.1.1"]["blockers"][0]


def test_diff_versions_reports_added_operations_and_parameter_changes(tmp_path):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    _write_spec(spec_dir / "9.1.1.json", version="9.1.1")
    _write_spec(spec_dir / "9.2.1.json", version="9.2.1", include_new_field=True, include_snapshot=True)
    db_path = tmp_path / "catalog.sqlite3"

    build_catalog(spec_dir, db_path)
    diff = diff_versions(db_path, old_version="9.1.1", new_version="9.2.1")

    assert {"method": "GET", "path": "/api/storage/snapshots"} in diff["added_operations"]
    assert any(item["path"] == "/api/storage/volumes" for item in diff["changed_parameters"])
