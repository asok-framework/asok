import importlib.util as _ut
import inspect
import os
import re


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
                "version": app.config.get("VERSION", "0.1.7"),
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

    def generate(self):
        """Scan the project's pages directory and build the complete OpenAPI specification.

        SECURITY: Depth limit prevents DoS via deeply nested directory structures.
        """
        pages_dir = os.path.join(self.app.root_dir, self.app.dirs["PAGES"])
        if not os.path.exists(pages_dir):
            return self.spec

        # SECURITY: Limit directory traversal depth to prevent DoS
        for root, _, files in os.walk(pages_dir):
            # Calculate depth relative to pages_dir
            depth = root[len(pages_dir):].count(os.sep)
            if depth >= self._MAX_DEPTH:
                continue

            for file in files:
                # SECURITY: Stop if we've reached the route limit
                if self._route_count >= self._MAX_ROUTES:
                    break

                if file.endswith(".py") and not file.startswith("__"):
                    rel_path = os.path.relpath(os.path.join(root, file), pages_dir)
                    route_path = "/" + rel_path[:-3].replace("\\", "/")

                    # SECURITY: Validate route path length before processing
                    if len(route_path) > 500:
                        continue

                    if route_path.endswith("/index"):
                        route_path = route_path[:-6] or "/"
                    if route_path.endswith("/page"):
                        route_path = route_path[:-5] or "/"

                    self._process_page(route_path, os.path.join(root, file))

        return self.spec

    def _process_page(self, route_path, full_path):
        # We need to load the module to inspect it
        # In Asok, we can use _load_module if we have a way to translate path to module name
        # For simplicity in the generator, we'll use a direct import logic

        mod_name = "api_scan_" + full_path.replace(os.sep, "_")
        spec = _ut.spec_from_file_location(mod_name, full_path)
        mod = _ut.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            return  # Skip if module fails to load

        methods = ["get", "post", "put", "patch", "delete"]
        path_item = {}

        for m in methods:
            fn = getattr(mod, m, None)
            if fn and hasattr(fn, "_asok_api"):
                meta = fn._asok_api
                operation = {
                    "summary": meta.summary or f"{m.upper()} {route_path}",
                    "description": meta.description or "",
                    "tags": meta.tags or ["General"],
                    "responses": {"200": {"description": "Successful Response"}},
                }

                if meta.input:
                    schema_name = self._register_schema(meta.input)
                    operation["x-input-schema"] = schema_name
                    operation["_input_schema"] = schema_name
                    if m in ["post", "put", "patch"]:
                        operation["requestBody"] = {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": f"#/components/schemas/{schema_name}"
                                    }
                                }
                            }
                        }
                    else:
                        operation["parameters"] = self._schema_to_params(meta.input)

                if meta.output:
                    schema_name = self._register_schema(meta.output)
                    operation["x-output-schema"] = schema_name
                    operation["_output_schema"] = schema_name
                    operation["responses"]["200"]["content"] = {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                        }
                    }

                path_item[m] = operation

        if path_item:
            # SECURITY: Check route count limit
            if self._route_count >= self._MAX_ROUTES:
                return

            # Handle dynamic routes: profile/[username:int].py -> /profile/{username}
            # SECURITY: Validate route_path length before regex to prevent ReDoS
            if len(route_path) > 500:
                return

            clean_path = re.sub(r"\[([^\]:]+)(?::[^\]]+)?\]", r"{\1}", route_path)
            self.spec["paths"][clean_path] = path_item
            self._route_count += 1

    def _register_schema(self, schema_cls):
        """Translate an Asok Schema class into an OpenAPI component schema.

        SECURITY: Schema count limit prevents DoS via excessive schema generation.
        """
        # SECURITY: Check schema count limit
        if self._schema_count >= self._MAX_SCHEMAS:
            return "UnknownSchema"

        if not inspect.isclass(schema_cls):
            # Might be an instance or a list
            if isinstance(schema_cls, list) and schema_cls:
                schema_cls = schema_cls[0]
            elif hasattr(schema_cls, "__class__"):
                schema_cls = schema_cls.__class__

        name = schema_cls.__name__
        if name in self.rendered_schemas:
            return name

        schema_def = {"type": "object", "properties": {}}
        required = []

        # Access _fields from SchemaMeta
        fields = getattr(schema_cls, "_fields", {})

        # SECURITY: Limit number of fields per schema to prevent DoS (max 200 fields)
        field_count = 0
        for f_name, field in fields.items():
            if field_count >= 200:
                break
            prop = self._field_to_openapi(field)
            schema_def["properties"][f_name] = prop
            if not getattr(field, "nullable", True):
                required.append(f_name)
            field_count += 1

        if required:
            schema_def["required"] = required

        self.spec["components"]["schemas"][name] = schema_def
        self.rendered_schemas[name] = True
        self._schema_count += 1
        return name

    def _field_to_openapi(self, field):
        f_type = field.sql_type
        res = {"type": "string"}  # Default

        if f_type == "INTEGER":
            res = {"type": "integer"}
        elif f_type == "REAL":
            res = {"type": "number"}

        if getattr(field, "is_boolean", False):
            res = {"type": "boolean"}
        elif getattr(field, "is_json", False):
            res = {"type": "object"}
        elif getattr(field, "is_datetime", False):
            res = {"type": "string", "format": "date-time"}
        elif getattr(field, "is_email", False):
            res = {"type": "string", "format": "email"}

        return res

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
