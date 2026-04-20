# API Development

Asok provides tools for building robust JSON APIs with consistent response formats and automatic documentation.

## Standard Responses

### `request.api(data, status=200)`

Returns a standardized success response.

```python
from asok import Request

def render(request: Request):
    users = User.all()
    return request.api([u.to_dict() for u in users])
```

**Response body:**
```json
{"data": [...], "status": 200}
```

### `request.api_error(message, status=400, errors=None)`

Returns a standardized error response.

```python
from asok import Request

def render(request):
    if not request.form.get("email"):
        return request.api_error("Validation failed", errors={"email": "required"})
```

**Response body:**
```json
{"error": "Validation failed", "status": 400, "errors": {"email": "required"}}
```

## API Security & CORS

When building an API, securing who can access your data is critical. Asok provides built-in mechanisms to handle Cross-Origin Resource Sharing (CORS) and rate limiting.

### Authorized Domains (CORS)

If your API is consumed by a frontend application hosted on a different domain or port (e.g., a React/Vue app on `localhost:3000`), the browser will block the request by default.

To authorize external domains, configure the `CORS_ORIGINS` setting in your app:

```python
# Allow specific domains (Recommended for production)
app.config["CORS_ORIGINS"] = [
    "https://myfrontend.com",
    "https://admin.myfrontend.com"
]

# Allow all domains (Useful for public APIs)
app.config["CORS_ORIGINS"] = "*"
```
For more advanced scenarios, see the [CORS & Gzip Guide](15-cors-gzip.md).

### Rate Limiting

To prevent abuse or DoS attacks on your API endpoints, protect them using the `@RateLimit` decorator.

```python
from asok import api, RateLimit

@api(summary="Fetch User Data")
@RateLimit(limit=60, window=60) # Max 60 requests per minute
def get(request, user_id: int):
    return request.api({"id": user_id, "name": "John Doe"})
```

### Internal Endpoints

If you are building an interactive UI (like an auto-completing search bar) that queries the server via AJAX, you might want to ensure the API endpoint is **only** accessible to your frontend application, and block external tools like Postman or scripts.

You can restrict access using the `@internal_only` decorator:

```python
from asok import Request, api, internal_only

@api(summary="Realtime Search (Internal)")
@internal_only
def get(request: Request):
    query = request.query.get("q", "")
    # Your search logic here
    return request.api({"results": []})
```

**Note:** For this to work, your frontend must send the `X-Requested-With: XMLHttpRequest` header when making the fetch request.

## Auto-Generated Documentation

Asok automatically generates an OpenAPI 3.0 specification and a premium, interactive documentation interface for your project.

- **Docs UI:** `/docs` (by default)
- **OpenAPI Spec:** `/openapi.json`

### Disabling the Documentation

By default, the API documentation is automatically enabled whenever the application is running in `DEBUG` mode. If you need to explicitly disable the documentation (for example, in a production environment), you can do so in two ways:

**1. Via Environment Variable (Recommended for Production)**
Set `ASOK_DOCS=false` or `DOCS=false` when starting your server:
```bash
ASOK_DOCS=false asok run
```

**2. Via Application Configuration**
Explicitly set it to `False` in your configuration code:
```python
app.config["DOCS"] = False
```
When disabled, the `/docs` and `/openapi.json` endpoints will return a `404 Not Found` error.

### Branding & Customization

You can customize the appearance of your API documentation via your application configuration or `.env` file. Asok will automatically fall back to global project settings (`PROJECT_NAME`, `SITE_LOGO`) if API-specific ones aren't provided.

```python
# In your app entry point (e.g., wsgi.py)
app.config["API_TITLE"] = "RTNC API"
app.config["API_DESCRIPTION"] = "API for RTNC application"
app.config["API_LOGO"] = "uploads/images/django.svg"
```

| Key | Description | Default |
|-----|-------------|---------|
| `API_TITLE` | The title displayed in the dashboard and browser tab. | `PROJECT_NAME` or `Asok API` |
| `API_LOGO` | Path to a custom logo (relative to static or full URL). | `SITE_LOGO` or Asok Logo |
| `API_DESCRIPTION` | Verbose description shown in the Introduction section. | Generic Asok description |
| `DOCS_PATH` | The URL path where the documentation is served. | `/docs` |
| `OPENAPI_PATH` | The URL path where the raw JSON spec is served. | `/openapi.json` |

### The "Try it out" Suite

The documentation includes a cohesive testing suite for every endpoint. It features:

- **Unified JSON Input**: A single input field for all parameters. The engine intelligently maps your keys to Path variables (like `{id}`), Query strings, or the Request Body.
- **Auto-Fills**: Clicking "Try it out" automatically generates a JSON example based on your input schema.
- **Smart Filtering**: System fields like `id`, `created_at`, and `slug` are automatically filtered out of examples to prevent clutter.
- **Secure Testing**: Built-in CSRF protection allows you to test `POST`, `PUT`, and `DELETE` requests directly from the UI.

### The `@api` Decorator

Use the `@api` decorator to add metadata to your endpoint handlers. This metadata is used to populate the reference.

```python
from asok import Request, api

@api(
    summary="Create Task",
    description="Adds a new task to the user's list.",
    tags=["Tasks"],
    input=TaskSchema,
    output=TaskSchema
)
def post(request: Request):
    data = request.json_body
    task = Task.create(**data)
    return request.api(TaskSchema().dump(task), status=201)
```

#### Metadata Fields:
- `summary` (or `name`): A short header for the operation.
- `description`: A detailed explanation (supports Markdown).
- `tags`: Used to group related operations together in the sidebar.
- `input`: A Schema class for request body generation and validation.
- `output`: A Schema class for response documentation.

## Request Validation

When you provide an `input` schema to the `@api` decorator, Asok automatically validates the incoming request body. You can safely access the parsed data via `request.json_body`.

## Model Serialization

For complex models, use the `Schema` class to control which fields are exposed and how they are formatted. This ensures your API responses are clean and consistent.

See the [Serialization Guide](29-serialization.md) for full details on building Schemas.

---
[← Previous: Request Logger](19-logger.md) | [Documentation](README.md) | [Next: File Storage →](21-file-storage.md)

