import importlib.util as _ut
import inspect
import os
import re
from typing import Any


class OpenAPIGenerator:
    """Engine for automatically generating OpenAPI 3.0 specifications from Asok API routes.

    SECURITY: Limits prevent DoS via excessive routes/schemas/depth.
    """

    # SECURITY: Maximum limits to prevent DoS
    _MAX_ROUTES = 1000
    _MAX_SCHEMAS = 500
    _MAX_DEPTH = 10

    def __init__(self, app):
        self.app = app
        self.spec = {
            "openapi": "3.0.0",
            "info": {
                "title": app.config.get(
                    "API_TITLE", app.config.get("PROJECT_NAME", "Asok API")
                ),
                "version": app.config.get("VERSION", "0.5.1"),
                "description": app.config.get(
                    "API_DESCRIPTION",
                    "A sleek, automatically generated reference for your Asok API endpoints.",
                ),
            },
            "paths": {},
            "components": {"schemas": {}},
        }
        self.rendered_schemas = {}
        self._route_count = 0
        self._schema_count = 0

    def _clean_suffixes(self, route_path: str) -> str:
        if route_path.endswith("/index"):
            return route_path[:-6] or "/"
        if route_path.endswith("/page"):
            return route_path[:-5] or "/"
        return route_path

    def _sanitize_route_path(self, rel_path: str) -> str:
        route_path = "/" + rel_path[:-3].replace("\\", "/")
        if len(route_path) > 500:
            return ""
        return self._clean_suffixes(route_path)

    def _process_file_if_route(self, root: str, file: str, pages_dir: str) -> None:
        if file.endswith(".py") and not file.startswith("__"):
            rel_path = os.path.relpath(os.path.join(root, file), pages_dir)
            route_path = self._sanitize_route_path(rel_path)
            if route_path:
                self._process_page(route_path, os.path.join(root, file))

    def _scan_files(self, root: str, files: list[str], pages_dir: str) -> None:
        for file in files:
            if self._route_count >= self._MAX_ROUTES:
                break
            self._process_file_if_route(root, file, pages_dir)

    def generate(self):
        """Scan the project's pages directory and build the complete OpenAPI specification.

        SECURITY: Depth limit prevents DoS via deeply nested directory structures.
        """
        pages_dir = os.path.join(self.app.root_dir, self.app.dirs["PAGES"])
        if not os.path.exists(pages_dir):
            return self.spec

        for root, _, files in os.walk(pages_dir):
            depth = root[len(pages_dir) :].count(os.sep)
            if depth >= self._MAX_DEPTH:
                continue
            self._scan_files(root, files, pages_dir)

        return self.spec

    def _load_module(self, full_path: str) -> Any:
        mod_name = "api_scan_" + full_path.replace(os.sep, "_")
        spec = _ut.spec_from_file_location(mod_name, full_path)
        mod = _ut.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            return None

    def _build_base_operation(self, meta: Any, m: str, route_path: str) -> dict[str, Any]:
        return {
            "summary": meta.summary or f"{m.upper()} {route_path}",
            "description": meta.description or "",
            "tags": meta.tags or ["General"],
            "responses": {"200": {"description": "Successful Response"}},
        }

    def _add_input_schema(self, operation: dict, input_schema: Any, m: str) -> None:
        schema_name = self._register_schema(input_schema)
        operation["x-input-schema"] = schema_name
        operation["_input_schema"] = schema_name
        if m in ["post", "put", "patch"]:
            operation["requestBody"] = {
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                    }
                }
            }
        else:
            operation["parameters"] = self._schema_to_params(input_schema)

    def _add_output_schema(self, operation: dict, output_schema: Any) -> None:
        schema_name = self._register_schema(output_schema)
        operation["x-output-schema"] = schema_name
        operation["_output_schema"] = schema_name
        operation["responses"]["200"]["content"] = {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"}
            }
        }

    def _parse_method_operation(self, fn: Any, m: str, route_path: str) -> dict[str, Any] | None:
        if not (fn and hasattr(fn, "_asok_api")):
            return None
        meta = fn._asok_api
        operation = self._build_base_operation(meta, m, route_path)
        if meta.input:
            self._add_input_schema(operation, meta.input, m)
        if meta.output:
            self._add_output_schema(operation, meta.output)
        return operation

    def _register_path_item(self, route_path: str, path_item: dict) -> None:
        if not path_item:
            return
        if self._route_count >= self._MAX_ROUTES:
            return
        if len(route_path) > 500:
            return
        clean_path = re.sub(r"\[([^\]:]+)(?::[^\]]+)?\]", r"{\1}", route_path)
        self.spec["paths"][clean_path] = path_item
        self._route_count += 1

    def _process_page(self, route_path, full_path):
        mod = self._load_module(full_path)
        if not mod:
            return

        path_item = {}
        for m in ["get", "post", "put", "patch", "delete"]:
            fn = getattr(mod, m, None)
            op = self._parse_method_operation(fn, m, route_path)
            if op:
                path_item[m] = op

        self._register_path_item(route_path, path_item)

    def _resolve_schema_class(self, schema_cls: Any) -> type:
        if inspect.isclass(schema_cls):
            return schema_cls
        if isinstance(schema_cls, list) and schema_cls:
            return schema_cls[0]
        if hasattr(schema_cls, "__class__"):
            return schema_cls.__class__
        return schema_cls

    def _build_properties_and_required(self, fields: dict) -> tuple[dict, list[str]]:
        properties = {}
        required = []
        field_count = 0
        for f_name, field in fields.items():
            if field_count >= 200:
                break
            properties[f_name] = self._field_to_openapi(field)
            if not getattr(field, "nullable", True):
                required.append(f_name)
            field_count += 1
        return properties, required

    def _register_schema(self, schema_cls):
        """Translate an Asok Schema class into an OpenAPI component schema.

        SECURITY: Schema count limit prevents DoS via excessive schema generation.
        """
        if self._schema_count >= self._MAX_SCHEMAS:
            return "UnknownSchema"

        schema_cls = self._resolve_schema_class(schema_cls)
        name = schema_cls.__name__
        if name in self.rendered_schemas:
            return name

        schema_def = {"type": "object", "properties": {}}
        fields = getattr(schema_cls, "_fields", {})
        properties, required = self._build_properties_and_required(fields)
        schema_def["properties"] = properties
        if required:
            schema_def["required"] = required

        self.spec["components"]["schemas"][name] = schema_def
        self.rendered_schemas[name] = True
        self._schema_count += 1
        return name

    def _detect_field_format(self, field) -> dict[str, str]:
        if getattr(field, "is_boolean", False):
            return {"type": "boolean"}
        if getattr(field, "is_json", False):
            return {"type": "object"}
        if getattr(field, "is_datetime", False):
            return {"type": "string", "format": "date-time"}
        if getattr(field, "is_email", False):
            return {"type": "string", "format": "email"}
        return {"type": "string"}

    def _field_to_openapi(self, field):
        f_type = field.sql_type
        if f_type == "INTEGER":
            return {"type": "integer"}
        if f_type == "REAL":
            return {"type": "number"}
        return self._detect_field_format(field)

    def _schema_to_params(self, schema_cls):
        params = []
        fields = getattr(schema_cls, "_fields", {})
        for f_name, field in fields.items():
            params.append(
                {
                    "name": f_name,
                    "in": "query",
                    "required": not getattr(field, "nullable", True),
                    "schema": self._field_to_openapi(field),
                }
            )
        return params
