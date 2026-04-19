# Serialization (Schema)

The `Schema` class (found in `asok.validation`) allows you to define how your data (Models or Dicts) should be transformed into JSON-serializable structures (`dump`) and how raw data should be extracted and cleaned (`load`).

## Basic Example

```python
from asok import Schema, Field

class UserSchema(Schema):
    name  = Field.String()
    email = Field.Email()
    bio   = Field.Text()

# Serialization (Model -> Dict)
user = User.find(id=1)
data = UserSchema().dump(user)
# {"name": "John", "email": "john@example.com", "bio": "..."}

# Deserialization (Dict -> Dict)
# Useful for extracting only the fields defined in the schema
input_data = request.json()
clean_data = UserSchema().load(input_data)
```

## Collections (`many=True`)

To serialize a list of objects, pass `many=True` to the constructor:

```python
users = User.all()
data = UserSchema(many=True).dump(users)
# [{"name": "John", ...}, {"name": "Jane", ...}]
```

## Auto-formatting

- **Dates & DateTimes**: Automatically converted to ISO 8601 strings.
- **Fields**: Only fields defined in the `Schema` class will be included in the output.

## Why use Schemas instead of `to_dict()`?

While `Model.to_dict()` is convenient, `Schema` offers more control:
1. **Filtering**: You can exclude sensitive fields (like `password_hash`).
2. **Context**: You can have multiple schemas for the same model (e.g., `UserPublicSchema` vs `UserPrivateSchema`).
3. **Consistency**: Works with both Models and raw dictionaries.

## Integration with API Responses

```python
from asok import Schema, Field

class PostSchema(Schema):
    title = Field.String()
    slug  = Field.String()

def render(request):
    posts = Post.all(published=True)
    return request.json(PostSchema(many=True).dump(posts))
```

---
[← Previous: WebSockets](28-websockets.md) | [Documentation](README.md) | [Next: Optimization →](30-optimization.md)
