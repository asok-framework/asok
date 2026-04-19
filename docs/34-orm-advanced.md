# Advanced ORM Features

Asok's ORM is designed to be powerful yet lightweight. This guide covers advanced field types and query patterns added for professional applications.

## 1. Advanced Field Types

### JSON Field
Store complex data structures (dicts, lists) transparently.
```python
class Settings(Model):
    config = Field.JSON(default={})

# Usage
s = Settings.create(config={"theme": "dark", "notifications": True})
print(s.config["theme"]) # "dark" (automatically a dict)
```

### Enum Field
Integrate with Python's standard `enum.Enum` for type-safe choices.
```python
import enum

class Status(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"

class User(Model):
    status = Field.Enum(Status, default=Status.PENDING)
```

### Decimal Field
Precise math for financial data (money).
```python
from decimal import Decimal

class Product(Model):
    price = Field.Decimal(precision=2)

p = Product.create(price=Decimal("19.99"))
```

### UUID Field
Automatically generate unique identifiers.
```python
class Job(Model):
    uid = Field.UUID() # Auto-generates uuid4 on save
```

### Tel / Phone Field
Store validated phone numbers.
```python
class User(Model):
    phone = Field.Tel(unique=True)
```

### Rich Text (WYSIWYG)
Upgrade standard textareas to a full-featured editor in the admin panel.
```python
class Post(Model):
    body = Field.Text(wysiwyg=True) # Enables Quill editor in Admin
```

---

## 2. Advanced Querying

### OR Conditions
```python
User.query().where("status", "active").or_where("is_admin", True).get()
```

### Range & NULL Checks
```python
# Between
Product.query().where_between("price", 10, 50).get()

# NULL checks
User.query().where_null("deleted_at").get()
User.query().where_not_null("email_verified_at").get()
```

### Sorting Shorthands
```python
latest_users = User.query().latest().limit(5).get()
oldest_tasks = Task.query().oldest().get()
```

## 3. Native Vector Search (Semantic)

Asok supports storing and searching high-dimensional vectors (embeddings) directly in SQLite using specialized binary storage and custom similarity functions.

### Defining a Vector Field
Specify the dimensions of your embedding (e.g., 1536 for OpenAI, 384 for standard local models).
```python
class Document(Model):
    content = Field.String()
    embedding = Field.Vector(dimensions=1536)
```

### Proximity Searching
Use `.nearest()` to find rows most similar to a query vector.
```python
query_vec = [0.1, 0.5, ...] # List of floats
docs = Document.query().nearest("embedding", query_vec, metric="cosine", limit=5).get()
```
*Metrics supported: `cosine` (default) and `euclidean`.*

### Admin Integration
You can enable semantic search in the Admin search bar!
1. Add `vector_search_field` to your `Admin` class.
2. Define an `embed_query` method on your model to convert search text into a vector.

```python
class Product(Model):
    name = Field.String()
    vec = Field.Vector(1536)

    @classmethod
    def embed_query(cls, text):
        # Call OpenAI / Mistral / Local model here
        return [0.1, 0.2, ...] 

    class Admin:
        search_fields = ["name"]
        vector_search_field = "vec"
```

---

## 4. High-Performance Model Methods

### Atomic Increments/Decrements
Safely update counters directly in SQL to avoid race conditions.
```python
post = Post.find(1)
post.increment("views")  # SQL: UPDATE posts SET views = views + 1 ...
```

### Reloading Data
```python
post.refresh() # Reloads attributes from the database
```

### API Error Handling
Perfect for REST controllers:
```python
def get(request):
    try:
        user = User.find_or_fail(request.params["id"])
        return request.api(user.to_dict())
    except ModelError:
        return request.api_error("User not found", status=404)
```

---
[← Previous: Advanced Deployment](33-deployment.md) | [Documentation](README.md) | [Next: Advanced Forms →](35-forms-advanced.md)
