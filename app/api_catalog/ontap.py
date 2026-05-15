from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a project dependency.
    yaml = None


HTTP_METHODS = {"get", "post", "patch", "delete", "put", "options", "head"}
DEFAULT_PRODUCT = "ontap"
DEFAULT_VERSIONS = (
    "9.9.1",
    "9.10.1",
    "9.11.1",
    "9.12.1",
    "9.13.1",
    "9.14.1",
    "9.15.1",
    "9.16.1",
    "9.17.1",
    "9.18.1",
)
DEFAULT_SPEC_DIR = Path("api_catalog/ontap/specs")
DEFAULT_DB_PATH = Path("api_catalog/ontap/ontap_api_catalog.sqlite3")
DEFAULT_MANIFEST = Path("api_catalog/manifests/lab-builder-netapp.yml")
NETAPP_SWAGGER_URL = "https://docs.netapp.com/us-en/ontap-restapi-{docs_key}/ontap/swagger-ui/index.html"


class CatalogError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def version_sort_key(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version or ""))
    return tuple(int(part) for part in parts)


def ontap_docs_key(version: str) -> str:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(version or ""))
    if not match:
        raise CatalogError(f"Invalid ONTAP version: {version}")
    return "".join(match.groups())


def normalize_version(value: str) -> str:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return ".".join(match.groups()) if match else str(value or "").strip()


def normalize_path(path: str, base_path: str = "/api") -> str:
    raw = "/" + str(path or "").strip().lstrip("/")
    base = "/" + str(base_path or "/api").strip().strip("/")
    if base == "/":
        base = ""
    if base and not raw.startswith(base + "/") and raw != base:
        raw = base + raw
    return re.sub(r"/+", "/", raw)


def path_signature(path: str) -> str:
    return re.sub(r"\{[^/{}]+\}", "{}", normalize_path(path))


def operation_risk(method: str) -> str:
    normalized = method.lower()
    if normalized in {"get", "head", "options"}:
        return "read"
    if normalized == "delete":
        return "delete"
    return "write"


def parse_versions(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return list(DEFAULT_VERSIONS)
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value.strip())
    else:
        items = list(value)
    return [normalize_version(str(item)) for item in items if str(item).strip()]


def swagger_url_for_version(version: str) -> str:
    return NETAPP_SWAGGER_URL.format(docs_key=ontap_docs_key(version))


def extract_swagger_spec_from_html(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script\s+id=["\']SwaggerYAML["\']\s+type=["\']application/json["\']>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        raise CatalogError("Swagger JSON script tag was not found in the NetApp docs page.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"NetApp Swagger JSON could not be parsed: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        raise CatalogError("Swagger payload does not contain a paths object.")
    return payload


def fetch_swagger_spec(version: str, *, timeout: int = 60) -> tuple[dict[str, Any], str]:
    url = swagger_url_for_version(version)
    request = Request(url, headers={"User-Agent": "lab-builder-api-catalog/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise CatalogError(f"NetApp docs returned HTTP {exc.code} for ONTAP {version}: {url}") from exc
    except URLError as exc:
        raise CatalogError(f"Could not fetch NetApp docs for ONTAP {version}: {exc}") from exc
    return extract_swagger_spec_from_html(body), url


def write_spec(spec: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "wt", encoding="utf-8") as handle:
        json.dump(spec, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise CatalogError(f"{path} does not contain a JSON object.")
    return payload


def fetch_versions(versions: Iterable[str], spec_dir: Path = DEFAULT_SPEC_DIR, *, compress: bool = True) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for version in sorted({normalize_version(item) for item in versions}, key=version_sort_key):
        spec, source_url = fetch_swagger_spec(version)
        spec["x-lab-builder-source-url"] = source_url
        destination = spec_dir / f"{version}.json.gz" if compress else spec_dir / f"{version}.json"
        write_spec(spec, destination)
        results.append(
            {
                "version": version,
                "source_url": source_url,
                "path": str(destination),
                "paths": len(spec.get("paths") or {}),
            }
        )
    return results


def import_spec_file(
    source_path: Path,
    version: str,
    spec_dir: Path = DEFAULT_SPEC_DIR,
    *,
    source_url: str = "",
    compress: bool = True,
) -> dict[str, Any]:
    if source_path.suffix == ".gz" or source_path.suffix == ".json":
        spec = read_json(source_path)
    else:
        spec = extract_swagger_spec_from_html(source_path.read_text(encoding="utf-8"))
    normalized_version = normalize_version(version)
    if source_url:
        spec["x-lab-builder-source-url"] = source_url
    destination = spec_dir / f"{normalized_version}.json.gz" if compress else spec_dir / f"{normalized_version}.json"
    write_spec(spec, destination)
    return {
        "version": normalized_version,
        "source": str(source_path),
        "path": str(destination),
        "paths": len(spec.get("paths") or {}),
    }


def _json_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _resolve_ref(spec: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    ref = str(item.get("$ref") or "")
    if not ref.startswith("#/"):
        return item
    current: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            return item
        current = current.get(part)
    return current if isinstance(current, dict) else item


def _schema_ref(schema: dict[str, Any] | None) -> str:
    if not isinstance(schema, dict):
        return ""
    if "$ref" in schema:
        return str(schema.get("$ref") or "")
    if "type" in schema:
        return str(schema.get("type") or "")
    return ""


def _operation_request_schema(operation: dict[str, Any], spec: dict[str, Any]) -> str:
    for raw_param in list(operation.get("parameters") or []):
        param = _resolve_ref(spec, raw_param) if isinstance(raw_param, dict) else {}
        if param.get("in") == "body":
            return _schema_ref(param.get("schema"))
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content") or {}
        if isinstance(content, dict):
            for media_type in ("application/json", "application/hal+json"):
                schema = ((content.get(media_type) or {}).get("schema")) if isinstance(content.get(media_type), dict) else None
                if schema:
                    return _schema_ref(schema)
    return ""


def _operation_response_schema(operation: dict[str, Any]) -> str:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return ""
    for status in sorted(responses):
        if not str(status).startswith("2"):
            continue
        response = responses.get(status) or {}
        if not isinstance(response, dict):
            continue
        if isinstance(response.get("schema"), dict):
            return _schema_ref(response.get("schema"))
        content = response.get("content") or {}
        if isinstance(content, dict):
            for media_type in ("application/json", "application/hal+json"):
                schema = ((content.get(media_type) or {}).get("schema")) if isinstance(content.get(media_type), dict) else None
                if schema:
                    return _schema_ref(schema)
    return ""


def _iter_parameters(spec: dict[str, Any], path_item: dict[str, Any], operation: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for raw_param in list(path_item.get("parameters") or []) + list(operation.get("parameters") or []):
        if isinstance(raw_param, dict):
            yield _resolve_ref(spec, raw_param)


def _parameter_type(param: dict[str, Any]) -> str:
    schema = param.get("schema")
    if isinstance(schema, dict):
        if schema.get("type"):
            return str(schema.get("type"))
        if schema.get("$ref"):
            return str(schema.get("$ref"))
    return str(param.get("type") or "")


def _parameter_enum(param: dict[str, Any]) -> str:
    enum = param.get("enum")
    schema = param.get("schema")
    if enum is None and isinstance(schema, dict):
        enum = schema.get("enum")
    return json.dumps(enum, sort_keys=True) if enum is not None else ""


def init_database(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS spec_versions;
        DROP TABLE IF EXISTS operations;
        DROP TABLE IF EXISTS parameters;
        DROP TABLE IF EXISTS operation_compatibility;

        CREATE TABLE spec_versions (
            product TEXT NOT NULL,
            version TEXT NOT NULL,
            title TEXT NOT NULL,
            base_path TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            built_at TEXT NOT NULL,
            PRIMARY KEY (product, version)
        );

        CREATE TABLE operations (
            product TEXT NOT NULL,
            version TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            path_signature TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            introduced TEXT NOT NULL,
            deprecated INTEGER NOT NULL,
            risk TEXT NOT NULL,
            summary TEXT NOT NULL,
            request_schema TEXT NOT NULL,
            response_schema TEXT NOT NULL,
            source_url TEXT NOT NULL,
            PRIMARY KEY (product, version, method, path)
        );

        CREATE INDEX idx_operations_lookup
            ON operations (product, version, method, path_signature);

        CREATE TABLE parameters (
            product TEXT NOT NULL,
            version TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            required INTEGER NOT NULL,
            type TEXT NOT NULL,
            introduced TEXT NOT NULL,
            enum_json TEXT NOT NULL,
            PRIMARY KEY (product, version, method, path, name, location)
        );

        CREATE INDEX idx_parameters_lookup
            ON parameters (product, version, method, path);

        CREATE TABLE operation_compatibility (
            product TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            path_signature TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            version_count INTEGER NOT NULL,
            versions_json TEXT NOT NULL,
            PRIMARY KEY (product, method, path)
        );
        """
    )


def _spec_version_from_path(path: Path) -> str:
    name = path.name
    if name.endswith(".json.gz"):
        return normalize_version(name[: -len(".json.gz")])
    if name.endswith(".json"):
        return normalize_version(name[: -len(".json")])
    return normalize_version(path.stem)


def _spec_paths(spec_dir: Path) -> list[Path]:
    by_version: dict[str, Path] = {}
    for path in list(spec_dir.glob("*.json")) + list(spec_dir.glob("*.json.gz")):
        version = _spec_version_from_path(path)
        if version not in by_version or path.suffix == ".gz":
            by_version[version] = path
    return [by_version[version] for version in sorted(by_version, key=version_sort_key)]


def build_catalog(
    spec_dir: Path = DEFAULT_SPEC_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    product: str = DEFAULT_PRODUCT,
) -> dict[str, Any]:
    specs = _spec_paths(spec_dir)
    if not specs:
        raise CatalogError(f"No ONTAP specs found under {spec_dir}. Run fetch first.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        init_database(conn)
        built_at = utc_now()
        operation_versions: dict[tuple[str, str], list[str]] = {}
        operation_count = 0
        parameter_count = 0

        for spec_path in specs:
            version = _spec_version_from_path(spec_path)
            spec = read_json(spec_path)
            info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
            title = str(info.get("title") or "ONTAP REST API")
            base_path = str(spec.get("basePath") or "/api")
            source_url = str(spec.get("x-lab-builder-source-url") or swagger_url_for_version(version))
            source_sha256 = _json_sha256(spec)
            conn.execute(
                """
                INSERT INTO spec_versions
                    (product, version, title, base_path, source_url, source_sha256, built_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (product, version, title, base_path, source_url, source_sha256, built_at),
            )

            paths = spec.get("paths") or {}
            for raw_path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                full_path = normalize_path(str(raw_path), base_path)
                signature = path_signature(full_path)
                for method, operation in path_item.items():
                    method_lower = str(method).lower()
                    if method_lower not in HTTP_METHODS or not isinstance(operation, dict):
                        continue
                    method_upper = method_lower.upper()
                    tag = ""
                    tags = operation.get("tags")
                    if isinstance(tags, list) and tags:
                        tag = str(tags[0])
                    summary = str(operation.get("summary") or "").replace("\n", " ").strip()
                    conn.execute(
                        """
                        INSERT INTO operations
                            (
                                product, version, method, path, path_signature, operation_id, tag,
                                introduced, deprecated, risk, summary, request_schema,
                                response_schema, source_url
                            )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product,
                            version,
                            method_upper,
                            full_path,
                            signature,
                            str(operation.get("operationId") or ""),
                            tag,
                            str(operation.get("x-ntap-introduced") or ""),
                            1 if bool(operation.get("deprecated")) else 0,
                            operation_risk(method_lower),
                            summary[:500],
                            _operation_request_schema(operation, spec),
                            _operation_response_schema(operation),
                            source_url,
                        ),
                    )
                    operation_versions.setdefault((method_upper, full_path), []).append(version)
                    operation_count += 1

                    for param in _iter_parameters(spec, path_item, operation):
                        name = str(param.get("name") or "").strip()
                        location = str(param.get("in") or "").strip()
                        if not name or not location:
                            continue
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO parameters
                                (
                                    product, version, method, path, name, location,
                                    required, type, introduced, enum_json
                                )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                product,
                                version,
                                method_upper,
                                full_path,
                                name,
                                location,
                                1 if bool(param.get("required")) else 0,
                                _parameter_type(param),
                                str(param.get("x-ntap-introduced") or ""),
                                _parameter_enum(param),
                            ),
                        )
                        parameter_count += 1

        for (method, path), versions in sorted(operation_versions.items()):
            ordered_versions = sorted(set(versions), key=version_sort_key)
            conn.execute(
                """
                INSERT INTO operation_compatibility
                    (
                        product, method, path, path_signature, first_seen,
                        last_seen, version_count, versions_json
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product,
                    method,
                    path,
                    path_signature(path),
                    ordered_versions[0],
                    ordered_versions[-1],
                    len(ordered_versions),
                    json.dumps(ordered_versions),
                ),
            )

        conn.commit()
        return {
            "db_path": str(db_path),
            "versions": [_spec_version_from_path(path) for path in specs],
            "operations": operation_count,
            "parameters": parameter_count,
        }
    finally:
        conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def available_versions(db_path: Path = DEFAULT_DB_PATH, *, product: str = DEFAULT_PRODUCT) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT version FROM spec_versions WHERE product = ?", (product,)).fetchall()
        return sorted([str(row["version"]) for row in rows], key=version_sort_key)
    finally:
        conn.close()


def find_operation(
    conn: sqlite3.Connection,
    *,
    product: str,
    version: str,
    method: str,
    path: str,
) -> sqlite3.Row | None:
    normalized_method = str(method or "").upper()
    normalized_path = normalize_path(path)
    row = conn.execute(
        """
        SELECT * FROM operations
        WHERE product = ? AND version = ? AND method = ? AND path = ?
        """,
        (product, version, normalized_method, normalized_path),
    ).fetchone()
    if row:
        return row
    signature = path_signature(normalized_path)
    return conn.execute(
        """
        SELECT * FROM operations
        WHERE product = ? AND version = ? AND method = ? AND path_signature = ?
        """,
        (product, version, normalized_method, signature),
    ).fetchone()


def operation_parameters(
    conn: sqlite3.Connection,
    *,
    product: str,
    version: str,
    method: str,
    path: str,
) -> list[sqlite3.Row]:
    operation = find_operation(conn, product=product, version=version, method=method, path=path)
    if not operation:
        return []
    return conn.execute(
        """
        SELECT * FROM parameters
        WHERE product = ? AND version = ? AND method = ? AND path = ?
        """,
        (product, version, str(method).upper(), operation["path"]),
    ).fetchall()


def _field_supported(field: str, query_parameters: set[str]) -> bool:
    name = str(field or "").strip()
    if not name or name == "*":
        return True
    if name in query_parameters:
        return True
    if any(param.startswith(name + ".") for param in query_parameters):
        return True
    if "." in name:
        parent = name.rsplit(".", 1)[0]
        if parent in query_parameters:
            return True
    return False


def _field_set_supported(field_set: Iterable[str], parameters: list[sqlite3.Row]) -> tuple[bool, list[str]]:
    query_parameters = {str(row["name"]) for row in parameters if str(row["location"]) == "query"}
    missing = [field for field in field_set if not _field_supported(str(field), query_parameters)]
    return not missing, missing


def _normalize_field_set(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise CatalogError(f"Unsupported field set value: {value!r}")


def _iter_manifest_capabilities(manifest: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    capabilities = manifest.get("capabilities") or {}
    if isinstance(capabilities, dict):
        for name, value in capabilities.items():
            if isinstance(value, dict):
                yield str(name), value
        return
    if isinstance(capabilities, list):
        for item in capabilities:
            if isinstance(item, dict):
                yield str(item.get("name") or "unnamed"), item


def load_manifest(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise CatalogError("PyYAML is required to load API catalog manifests.")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise CatalogError(f"{path} must contain a YAML mapping.")
    return payload


def validate_manifest(
    manifest_path: Path = DEFAULT_MANIFEST,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    versions: Iterable[str] | None = None,
    product: str = DEFAULT_PRODUCT,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    manifest_product = str(manifest.get("product") or product)
    version_list = parse_versions(versions or manifest.get("required_versions") or available_versions(db_path, product=manifest_product))
    if not version_list:
        raise CatalogError("No versions were supplied and the catalog has no versions.")

    conn = _connect(db_path)
    try:
        result: dict[str, Any] = {
            "ok": True,
            "application": str(manifest.get("application") or ""),
            "product": manifest_product,
            "manifest": str(manifest_path),
            "db_path": str(db_path),
            "versions": {},
        }
        for version in version_list:
            blockers: list[str] = []
            warnings: list[str] = []
            checked = 0
            for capability_name, capability in _iter_manifest_capabilities(manifest):
                operations = capability.get("operations") or []
                if not isinstance(operations, list):
                    continue
                for operation_spec in operations:
                    if not isinstance(operation_spec, dict):
                        continue
                    checked += 1
                    method = str(operation_spec.get("method") or "").upper()
                    path = str(operation_spec.get("path") or "")
                    operation = find_operation(conn, product=manifest_product, version=version, method=method, path=path)
                    if operation is None:
                        message = f"{capability_name}: {method} {path} is not present in ONTAP {version}."
                        if operation_spec.get("required") is False:
                            warnings.append(message)
                        else:
                            blockers.append(message)
                        continue
                    field_sets = operation_spec.get("field_sets") or []
                    if not field_sets:
                        continue
                    parameters = operation_parameters(conn, product=manifest_product, version=version, method=method, path=path)
                    supported_sets: list[int] = []
                    missing_by_set: list[str] = []
                    for index, raw_field_set in enumerate(field_sets, start=1):
                        field_set = _normalize_field_set(raw_field_set)
                        supported, missing = _field_set_supported(field_set, parameters)
                        if supported:
                            supported_sets.append(index)
                        else:
                            missing_by_set.append(f"set {index} missing {', '.join(missing)}")
                    if not supported_sets:
                        blockers.append(
                            f"{capability_name}: {method} {path} has no supported field set in ONTAP {version} "
                            f"({'; '.join(missing_by_set)})."
                        )
                    elif supported_sets[0] != 1:
                        warnings.append(
                            f"{capability_name}: {method} {path} uses fallback field set {supported_sets[0]} in ONTAP {version}."
                        )
            version_ok = not blockers
            if not version_ok:
                result["ok"] = False
            result["versions"][version] = {
                "ok": version_ok,
                "checked_operations": checked,
                "blockers": blockers,
                "warnings": warnings,
            }
        return result
    finally:
        conn.close()


def diff_versions(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    old_version: str,
    new_version: str,
    product: str = DEFAULT_PRODUCT,
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        def operations(version: str) -> set[tuple[str, str]]:
            rows = conn.execute(
                "SELECT method, path FROM operations WHERE product = ? AND version = ?",
                (product, version),
            ).fetchall()
            return {(str(row["method"]), str(row["path"])) for row in rows}

        old_ops = operations(old_version)
        new_ops = operations(new_version)
        common = old_ops & new_ops
        changed_parameters: list[dict[str, Any]] = []
        for method, path in sorted(common):
            old_params = {
                (str(row["location"]), str(row["name"]), int(row["required"]), str(row["type"]))
                for row in conn.execute(
                    """
                    SELECT location, name, required, type FROM parameters
                    WHERE product = ? AND version = ? AND method = ? AND path = ?
                    """,
                    (product, old_version, method, path),
                ).fetchall()
            }
            new_params = {
                (str(row["location"]), str(row["name"]), int(row["required"]), str(row["type"]))
                for row in conn.execute(
                    """
                    SELECT location, name, required, type FROM parameters
                    WHERE product = ? AND version = ? AND method = ? AND path = ?
                    """,
                    (product, new_version, method, path),
                ).fetchall()
            }
            if old_params != new_params:
                changed_parameters.append(
                    {
                        "method": method,
                        "path": path,
                        "added_parameters": sorted([list(item) for item in new_params - old_params]),
                        "removed_parameters": sorted([list(item) for item in old_params - new_params]),
                    }
                )
        return {
            "product": product,
            "old_version": old_version,
            "new_version": new_version,
            "added_operations": [{"method": method, "path": path} for method, path in sorted(new_ops - old_ops)],
            "removed_operations": [{"method": method, "path": path} for method, path in sorted(old_ops - new_ops)],
            "changed_parameters": changed_parameters,
        }
    finally:
        conn.close()


def catalog_summary(db_path: Path = DEFAULT_DB_PATH, *, product: str = DEFAULT_PRODUCT) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        versions = available_versions(db_path, product=product)
        per_version: dict[str, dict[str, int]] = {}
        for version in versions:
            operations = conn.execute(
                "SELECT COUNT(*) AS count FROM operations WHERE product = ? AND version = ?",
                (product, version),
            ).fetchone()["count"]
            parameters = conn.execute(
                "SELECT COUNT(*) AS count FROM parameters WHERE product = ? AND version = ?",
                (product, version),
            ).fetchone()["count"]
            per_version[version] = {"operations": int(operations), "parameters": int(parameters)}
        return {"db_path": str(db_path), "product": product, "versions": versions, "per_version": per_version}
    finally:
        conn.close()


def _print_validation(result: dict[str, Any]) -> int:
    print(f"Manifest: {result['manifest']}")
    print(f"Catalog: {result['db_path']}")
    print(f"Application: {result.get('application') or 'unknown'}")
    for version, status in result["versions"].items():
        state = "OK" if status["ok"] else "BLOCKED"
        print(f"{version}: {state} ({status['checked_operations']} operations checked)")
        for warning in status["warnings"]:
            print(f"  warning: {warning}")
        for blocker in status["blockers"]:
            print(f"  blocker: {blocker}")
    return 0 if result["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate the offline ONTAP REST API catalog.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite catalog path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch ONTAP Swagger specs from NetApp docs.")
    fetch_parser.add_argument("--versions", default=",".join(DEFAULT_VERSIONS), help="Comma or space separated ONTAP versions.")
    fetch_parser.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR, help="Destination spec directory.")
    fetch_parser.add_argument("--uncompressed", action="store_true", help="Write .json files instead of compressed .json.gz files.")

    import_parser = subparsers.add_parser("import-spec", help="Import a saved OpenAPI JSON or Swagger UI HTML page.")
    import_parser.add_argument("--version", required=True, help="ONTAP version represented by the source file.")
    import_parser.add_argument("--source", type=Path, required=True, help="Source .json, .json.gz, or Swagger UI .html file.")
    import_parser.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR, help="Destination spec directory.")
    import_parser.add_argument("--source-url", default="", help="Optional URL the file was captured from.")
    import_parser.add_argument("--uncompressed", action="store_true", help="Write a .json file instead of compressed .json.gz.")

    build_parser = subparsers.add_parser("build", help="Build the SQLite catalog from local specs.")
    build_parser.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR, help="Local spec directory.")

    subparsers.add_parser("summary", help="Print catalog summary.")

    diff_parser = subparsers.add_parser("diff", help="Diff operations between two ONTAP versions.")
    diff_parser.add_argument("--from", dest="old_version", required=True, help="Source ONTAP version.")
    diff_parser.add_argument("--to", dest="new_version", required=True, help="Target ONTAP version.")
    diff_parser.add_argument("--limit", type=int, default=25, help="Maximum detailed rows per section.")

    validate_parser = subparsers.add_parser("validate", help="Validate an application manifest against the catalog.")
    validate_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Application API manifest.")
    validate_parser.add_argument("--versions", default="", help="Optional comma or space separated ONTAP versions.")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        for item in fetch_versions(parse_versions(args.versions), args.spec_dir, compress=not args.uncompressed):
            print(f"fetched ONTAP {item['version']}: {item['paths']} paths -> {item['path']}")
        return 0

    if args.command == "import-spec":
        item = import_spec_file(
            args.source,
            args.version,
            args.spec_dir,
            source_url=args.source_url,
            compress=not args.uncompressed,
        )
        print(f"imported ONTAP {item['version']}: {item['paths']} paths -> {item['path']}")
        return 0

    if args.command == "build":
        summary = build_catalog(args.spec_dir, args.db)
        print(
            f"built {summary['db_path']}: {len(summary['versions'])} versions, "
            f"{summary['operations']} operations, {summary['parameters']} parameters"
        )
        return 0

    if args.command == "summary":
        summary = catalog_summary(args.db)
        print(f"Catalog: {summary['db_path']}")
        for version, counts in summary["per_version"].items():
            print(f"{version}: {counts['operations']} operations, {counts['parameters']} parameters")
        return 0

    if args.command == "diff":
        diff = diff_versions(args.db, old_version=normalize_version(args.old_version), new_version=normalize_version(args.new_version))
        print(f"{diff['old_version']} -> {diff['new_version']}")
        print(f"added operations: {len(diff['added_operations'])}")
        for item in diff["added_operations"][: args.limit]:
            print(f"  + {item['method']} {item['path']}")
        print(f"removed operations: {len(diff['removed_operations'])}")
        for item in diff["removed_operations"][: args.limit]:
            print(f"  - {item['method']} {item['path']}")
        print(f"operations with parameter changes: {len(diff['changed_parameters'])}")
        for item in diff["changed_parameters"][: args.limit]:
            print(
                f"  * {item['method']} {item['path']} "
                f"(+{len(item['added_parameters'])} params, -{len(item['removed_parameters'])} params)"
            )
        return 0

    if args.command == "validate":
        versions = parse_versions(args.versions) if str(args.versions or "").strip() else None
        return _print_validation(validate_manifest(args.manifest, args.db, versions=versions))

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
