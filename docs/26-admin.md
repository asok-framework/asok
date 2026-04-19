# Admin

A Django-style admin interface, auto-generated from your models. Zero config to start, deeply customizable when you need it.

## Quick start

Scaffold a project with admin enabled:

```bash
asok create myapp --admin
cd myapp
asok createsuperuser
asok dev
```

Open http://127.0.0.1:8000/admin and log in.

The `--admin` flag generates a `User` model with an `is_admin` field and registers `Admin(app)` in `wsgi.py`.

## Adding to an existing project

If you decided to add the Admin interface after your project was created, run:

```bash
asok admin --enable
```

This command will:
1. Update `wsgi.py` to register the `Admin` extension.
2. Create `src/models/user.py` with the default `User` model if it doesn't exist.
3. Guide you through the necessary migrations and superuser creation.

### Customization

```python
Admin(app, site_name="codewithmpia", url_prefix="/gestion", favicon="images/logo.svg")
```

| Param | Default | Description |
|---|---|---|
| `site_name` | `"Asok Admin"` | Brand shown in sidebar / page title |
| `url_prefix` | `"/admin"` | URL prefix for all admin routes |
| `favicon` | `None` | Path to your brand logo / tab icon |

#### Smart Asset Resolution

The `favicon` path resolves intelligently based on its location:
- **Internal Assets**: If you use a simple filename like `logo.svg`, the admin uses its built-in internal version.
- **Project Assets**: If you provide a directory path (e.g. `images/logo.svg` or `uploads/brand.png`), the admin resolves it relative to your project's `src/partials/` folder.

This logo is automatically applied as the browser favicon, the sidebar brand icon, and the login page header.

## Authentication

Admin uses `request.login()` and requires the user to have `is_admin = True`. Add the field to your `User` model:

```python
from asok import Model, Field

class User(Model):
    email = Field.String()
    password = Field.Password()
    is_admin = Field.Boolean(default=False)
```

Create the first superuser:

```bash
asok createsuperuser
# or non-interactive:
asok createsuperuser --email=admin@example.com --password=secret
```

## Roles & permissions

Asok Admin ships with a role-based permission system. Two models are auto-provisioned when you call `Admin(app)`:

- **User** — the auth model (`email`, `password`, `name`, `is_admin`, `created_at`)
- **Role** — grants permissions to users (`name`, `label`, `permissions`, `created_at`)

Linked by a `role_user` pivot table (a user can have any number of roles).

### Permission format

Permissions are comma-separated strings following `<slug>.<verb>`:

- `posts.view`, `posts.edit`, `users.delete`, `articles.export`
- `posts.*` — all verbs on `posts`
- `*` — superuser (bypass all checks)

The available verbs are: `view`, `add`, `edit`, `delete`, `export`.

### Granting access

1. Log in as a superuser (`is_admin = True`) or an existing admin.
2. Go to **Roles** → **New**.
3. Give it a `name` (e.g. `editor`) and a `label` (e.g. `Content Editor`).
4. Tick boxes in the **Permissions** matrix (models × verbs). The "all" column ticks a whole row; the **Superuser** checkbox at the top grants `*`.
5. Save.
6. Go to **Users**, edit the target user, tick the role in the **Roles** section. Save.

The user now sees only the models they have `view` permission on. Each action (add / edit / delete / export) is gated by its corresponding verb.

### Behaviour summary

| User state | Access |
|---|---|
| `is_admin = True` | Full access, bypass all permission checks |
| Has roles with perms | Filtered access based on permissions |
| No roles, `is_admin = False` | Redirected to `/admin/login` |

### `user.can(perm)`

The `User.can()` helper is also available in your own code:

```python
if request.user.can("posts.edit"):
    ...
```

Supports exact match, `<slug>.*` wildcards, and `*` superuser.

### Self-protection

The admin interface prevents foot-gunning:

- You cannot delete your own account (via row action or bulk delete).
- You cannot demote yourself (your own `is_admin` field is hidden on self-edit).
- You cannot change your own role assignments.

### `createsuperuser`

```bash
asok createsuperuser
```

Creates a user with `is_admin = True` **and** an `admin` role (with `*` permissions) if the `Role` model exists. Attaches the role to the user automatically.

## Per-model configuration

Customize how a model appears in admin by adding a nested `Admin` class:

```python
class Post(Model):
    title = Field.String()
    body = Field.Text()
    slug = Field.Slug(source='title')
    published = Field.Boolean(default=False)
    author_id = Field.ForeignKey('users')
    content = Field.Text(wysiwyg=True) # Rich Text Editor

    author = BelongsTo('users')
    tags = BelongsToMany('tags')

    class Admin:
        label = "Posts"
        list_display = ['title', 'author', 'published', 'created_at']
        search_fields = ['title', 'body']
        list_filter = ['published', 'author_id']
        readonly_fields = ['slug', 'created_at']
        fieldsets = [
            ('Content', ['title', 'slug', 'body']),
            ('Publishing', ['published', 'author_id']),
        ]
        per_page = 25
        can_delete = True
        actions = ['publish_selected']

    @classmethod
    def publish_selected(cls, ids):
        cls.where('id', 'in', ids).update(published=True)
```

### All options

| Option | Type | Description |
|---|---|---|
| `hidden` | `bool` | Hide this model from admin (`hidden = True`) |
| `slug` | `str` | URL slug (defaults to table name) |
| `label` | `str` | Display name in sidebar |
| `group` | `str` | Group models together in the sidebar |
| `list_display` | `list` | Columns in the list view |
| `search_fields` | `list` | Fields searched by the search box (multi-field OR-LIKE) |
| `list_filter` | `list` | Fields exposed as filters in the sidebar |
| `readonly_fields` | `list` | Fields displayed but not editable |
| `fieldsets` | `list` | Group fields into labeled cards: `[(label, [fields]), ...]` |
| `per_page` | `int` | Pagination size (default 20) |
| `inlines` | `list` | Related models to display below the form (`['comments', ...]`) |
| `can_add` | `bool` | Allow creation (default `True`) |
| `can_edit` | `bool` | Allow editing (default `True`) |
| `can_delete` | `bool` | Allow deletion (default `True`) |
| `actions` | `list` | Names of `@classmethod`s exposed as bulk actions |

## Display helper

Foreign keys and relations are displayed using the related model's `__str__()` method, falling back to `name`, `title`, `label`, `email`, `username`, `slug`, then `#<id>`.

Define `__str__` to control the display:

```python
class User(Model):
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
```

## Features

- **Search** — multi-field LIKE across `search_fields`
- **Filters** — sidebar filter panel for `list_filter` fields
- **Sort** — click any column header
- **Pagination** — preserves search, filters, and sort
- **Bulk actions** — select rows and delete or run a custom action
- **Soft delete & trash** — restore or permanently delete from the trash view
- **CSV export** — exports the current filtered/sorted view
- **BelongsToMany editor** — checkbox grid for many-to-many relations
- **Rich Text Editor (WYSIWYG)** — supported on `Text` fields with `wysiwyg=True`
- **Inlines** — view related `HasMany` rows below the parent form, with edit links
- **Date / datetime / file pickers** — auto-detected from field types
- **Image preview** — preview uploaded images on the edit form
- **Breadcrumbs** — context-aware navigation
- **Save variants** — Save / Save and continue / Save and add another
- **Dark mode** — toggle in the topbar, persisted in localStorage
- **Empty values** — `—` shown for null/empty cells
- **Boolean badges** — colored Yes/No badges for boolean columns

## Templates and static files

Templates and CSS/JS are bundled inside the `asok` package. To override a template, drop a file with the same name into `src/templates/admin/` (e.g., `src/templates/admin/list.html`) — your template wins.

The admin CSS is self-contained (no Tailwind required) and supports light/dark themes via CSS variables.

---
[← Previous: Sessions](25-sessions.md) | [Documentation](README.md) | [Next: Tailwind CSS →](27-tailwind.md)
