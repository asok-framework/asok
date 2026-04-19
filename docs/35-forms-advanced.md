# Advanced Forms Features

Asok's form engine now supports modern HTML5 inputs and deep synchronization with the ORM's advanced fields.

## 1. Modern HTML5 Fields

Enjoy native browser widgets for specific data types:

```python
form = Form({
    "q": Form.search("Search"),
    "website": Form.url("Website"),
    "phone": Form.tel("Phone Number"),
    "theme_color": Form.color("Accent Color"),
    "volume": Form.range("Volume", min=0, max=100),
})
```

## 2. ORM Integration

### Enum Support
Automatically generate a dropdown from any Python `enum.Enum` class:

```python
import enum

class TaskStatus(enum.Enum):
    TODO = "todo"
    PROGRESS = "progress"
    DONE = "done"

form = Form({
    "status": Form.enum("Task Status", TaskStatus)
})
```

### JSON Fields
Asok provides a `textarea` that automatically includes a `json` validation rule.

```python
form = Form({
    "metadata": Form.json("Extra Data")
})
```

---

## 3. Automated Model Forms

The `Form.from_model()` helper now automatically maps advanced ORM types to their corresponding form widgets.

| ORM Field | Form Widget | Notes |
|---|---|---|
| `Field.Decimal(precision=2)` | `number` | Auto-calculates `step="0.01"` |
| `Field.Enum(MyEnum)` | `select` | Auto-populates choices from `MyEnum` |
| `Field.JSON()` | `textarea` | Auto-adds `json` validation rule |
| `Field.UUID()` | `text` | Sets to `readonly` by default |

### Example
```python
# Generate a professional form from your Model in one line
form = Form.from_model(Product, request)

if form.validate():
    Product.create(**form.data)
```

## 4. Custom Validation Rules

You can easily register custom rules that link your Forms to the Validator:

```python
from asok.validation import register_rule

def check_username(value, arg, data):
    return value.isalnum()

register_rule("username", check_username, "Username must be alphanumeric.")

# Use it in your form
form = Form({
    "username": Form.text("Username", rules="required|username")
})
```

---
[← Previous: Advanced ORM](34-orm-advanced.md) | [Documentation](README.md) | [Next: Vector Search →](36-vector-search.md)
