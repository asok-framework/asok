# ORM (Database)

Asok has a built-in SQLite ORM. Zero config — `db.sqlite3` is created automatically. Tables are auto-created from your model definitions on app start.

## Define a model

```python
# src/models/post.py
from asok import Model, Field, Relation

class Post(Model):
    title      = Field.String()
    body       = Field.String()
    slug       = Field.Slug(populate_from='title')
    published  = Field.Boolean(default=False)
    author_id  = Field.ForeignKey(lambda: User)
    cover      = Field.File(upload_to='posts')
    created_at = Field.CreatedAt()
    updated_at = Field.UpdatedAt()
    deleted_at = Field.SoftDelete()
```

## Field types

| Field | SQLite | Notes |
|---|---|---|
| `Field.String(max_length=255)` | TEXT | Short text — `<input type="text">` in admin / `Form.from_model()`. `max_length` defaults to 255 |
| `Field.Text(wysiwyg=False)` | TEXT | Long text — `<textarea>` in admin. Pass `wysiwyg=True` for rich text editor. |
| `Field.Slug(populate_from="title", always_update=False)` | TEXT | URL-friendly string. Set `always_update=True` to regenerate it every time the source field changes. |
| `Field.Email(max_length=255)` | TEXT | Validated email — `<input type="email">` and **rejected on save** if invalid |
| `Field.Integer()` | INTEGER | |
| `Field.Float(precision=2)` | REAL | `precision` controls form input `step` (e.g. `step="0.01"`). Alias: `Field.Double()` |
| `Field.Boolean()` | INTEGER | 0/1 — rendered as `<input type="checkbox">` |
| `Field.Date()` | TEXT | ISO format |
| `Field.DateTime()` | TEXT | ISO format |
| `Field.Password()` | TEXT | Auto-hashed (PBKDF2-SHA256, 100k) |
| `Field.ForeignKey(Model)` | INTEGER | FK to another model |
| `Field.File(upload_to='dir')` | TEXT | Stores filename, files saved under uploads/ |
| `Field.CreatedAt()` | TEXT | Set once on first save |
| `Field.UpdatedAt()` | TEXT | Updated on every save |
| `Field.SoftDelete()` | TEXT | Enables soft delete (see below) |
| `Field.Slug(populate_from='title')` | TEXT | Auto-generated URL slug |

### Choosing the right text field

Three text-flavored fields to pick from depending on intent — they all map to SQLite TEXT but they affect form rendering and validation:

```python
class Contact(Model):
    name    = Field.String(max_length=100)  # short text, <input type="text">
    email   = Field.Email()                  # email input + auto-validation
    message = Field.Text()                   # long text, <textarea>
```

`Field.Email()` validates the value on `save()` (and on `Model.create()`) using a basic regex. Invalid emails raise `ModelError`:

```python
Contact.create(email='not-an-email', message='hi')
# → ModelError: Email is not a valid email address.
```

This validation happens regardless of whether the value comes from a Form, an API call, or hand-written code. Forms generated via `Form.from_model()` also pick up the `email` validation rule automatically (see [Forms](06-forms.md#generate-a-form-from-a-model)).

### Field options

```python
Field.String(max_length=80, default='draft', unique=True, nullable=False)
```

All fields accept `default`, `unique`, and `nullable`. `String` and `Email` additionally accept `max_length`. `Float` accepts `precision`.

## CRUD

```python
# Create
post = Post.create(title='Hello', body='...')

# Read
Post.find(id=1)
Post.find(slug='hello')
Post.all()
Post.all(published=True, order_by='-created_at', limit=10)
Post.count(published=True)
Post.exists(slug='hello')

# Update
post.title = 'Updated'
post.save()

# Or set + save in one call (perfect with form.data)
post.update(title='Updated', body='...')

# Delete
post.delete()
```

### Find-or-create helpers

```python
Post.first_or_create(slug='hello', defaults={'title': 'Hello', 'body': '...'})
Post.update_or_create(slug='hello', defaults={'title': 'Updated'})
```

## Query builder

For anything more complex than equality filters, use the chainable `Query` builder:

```python
Post.query() \
    .where('views', '>', 100) \
    .where('published', True) \
    .order_by('-created_at') \
    .limit(20) \
    .get()

Post.where('title', 'LIKE', '%python%').get()
Post.where_in('id', [1, 2, 3]).get()
Post.like('title', '%python%').get() # Shortcut for LIKE
```

### Methods

| Method | Description |
|---|---|
| `.where(col, op, val)` | Add a WHERE clause (`=`, `!=`, `<`, `>`, `<=`, `>=`, `LIKE`, `NOT LIKE`, `IN`, `NOT IN`) |
| `.where(col, val)` | Shortcut for equality |
| `.where_in(col, [vals])` | WHERE col IN (...) |
| `.like(col, pattern)` | Shortcut for LIKE |
| `.order_by('col')` / `.order_by('-col')` | ASC / DESC |
| `.limit(n)` / `.offset(n)` | Pagination |
| `.with_('relation', ...)` | Eager-load relations (avoids N+1) |
| `.get()` | Execute, return list |
| `.first()` | Execute, return first or `None` |
| `.count()` | Aggregate count |
| `.sum('col')` | Sum values in column |
| `.avg('col')` | Average value |
| `.min('col')` | Minimum value |
| `.max('col')` | Maximum value |
| `.pluck('col')` | List of single column values |
| `.update(**vals)` | Bulk UPDATE |
| `.delete()` | Bulk DELETE |
| `.exists()` | Bool |

### Raw SQL

```python
Post.raw("SELECT * FROM posts WHERE views > ?", [100])
```

## Relationships

```python
class User(Model):
    name = Field.String()
    posts = Relation.HasMany('Post')
    profile = Relation.HasOne('Profile')
    roles = Relation.BelongsToMany('Role')

class Post(Model):
    author_id = Field.ForeignKey(lambda: User)
    author = Relation.BelongsTo('User')
```

| Relation | Returns | Default FK |
|---|---|---|
| `HasMany('Post')` | list | `<owner>_id` on the target |
| `HasOne('Profile')` | single or `None` | `<owner>_id` on the target |
| `BelongsTo('User')` | single or `None` | `<target>_id` on self |
| `BelongsToMany('Role')` | list | pivot table `<a>_<b>` (alphabetical) |

Access them as methods:

```python
user = User.find(id=1)
user.posts()       # list of Post
user.profile()     # Profile or None
user.roles()       # list of Role
post.author()      # User or None
```

### Custom keys

```python
Relation.HasMany('Post', foreign_key='writer_id')
Relation.BelongsToMany('Tag', pivot_table='post_tags', pivot_fk='post_id', pivot_other_fk='tag_id')
```

### Eager loading

Avoid N+1 queries by pre-loading relations:

```python
posts = Post.query().with_('author').limit(20).get()
for p in posts:
    p.author()  # served from cache, no extra query
```

### BelongsToMany: attach / detach / sync

```python
post.attach('tags', [1, 2, 3])     # add pivot rows
post.detach('tags', [2])           # remove specific
post.detach('tags')                # remove all
post.sync('tags', [4, 5])          # replace all with these ids
```

## Soft delete

Add `Field.SoftDelete()` to enable it on a model:

```python
class Post(Model):
    title = Field.String()
    deleted_at = Field.SoftDelete()
```

Now `delete()` only sets `deleted_at`. Soft-deleted rows are excluded from all queries by default.

```python
post.delete()              # soft delete
post.restore()             # un-delete
post.force_delete()        # permanent

Post.with_trashed().get()  # include deleted
Post.only_trashed().get()  # only deleted
```

## File fields

```python
class Post(Model):
    cover = Field.File(upload_to='posts')
```

The column stores the filename. The actual file is saved to `uploads/posts/<filename>` when set via a form. Use `request.files` and `UploadedFile.save()` for manual handling — see [File Storage](21-file-storage.md).

## Lifecycle hooks

Override any of these on your model:

```python
class Post(Model):
    def before_save(self): ...
    def after_save(self): ...
    def before_create(self): ...
    def after_create(self): ...
    def before_update(self): ...
    def after_update(self): ...
    def before_delete(self): ...
    def after_delete(self): ...
```

## Pagination

```python
result = Post.paginate(page=2, per_page=10, order_by='-created_at', published=True)

result['items']         # list of Post
result['total']         # total count
result['pages']         # total pages
result['current_page']  # 2
```

## Password hashing

`Field.Password()` auto-hashes on save (PBKDF2-SHA256, 100k iterations):

```python
class User(Model):
    email = Field.String(unique=True)
    password = Field.Password()

user = User.create(email='a@b.com', password='secret')
user.check_password('password', 'secret')  # True
```

## Slug auto-generation

```python
slug = Field.Slug(populate_from='title')
# "Hello World!" → "hello-world"
```

Generated on save if empty.

## Serialization

```python
post.to_dict()
# {"id": 1, "title": "Hello", ...}
```

For controlled API output, use `Schema` (see [API Helpers](20-api.md)):

```python
from asok import Schema, Field

class PostSchema(Schema):
    title = Field.String()
    slug  = Field.String()

PostSchema().dump(post)
PostSchema(many=True).dump(posts)
```

## Performance

- **Thread-local connections** — reused within the same thread
- **WAL mode** — concurrent reads while writing
- **Eager loading** — `.with_(...)` batches related queries
- **Auto-indexes** — `unique=True` and `ForeignKey` get indexed automatically
- **No configuration needed**

---
[← Previous: Template Engine](04-templates.md) | [Documentation](README.md) | [Next: Forms →](06-forms.md)
