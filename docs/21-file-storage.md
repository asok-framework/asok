# File Storage

## UploadedFile

Multipart file uploads are parsed into `UploadedFile` objects available on `request.files`.

```python
def render(request):
    photo = request.files.get("photo")
    if photo:
        print(photo.filename)  # "avatar.jpg"
        print(photo.size)      # 102400
        saved = photo.save("avatar.jpg")
        # Returns actual path (may differ if conflict)
```

### Properties

| Property | Type | Description |
|---|---|---|
| `filename` | `str` | Original upload filename |
| `content` | `bytes` | Raw file content |
| `size` | `int` | File size in bytes |

### `save(destination)`

Saves the file to disk. By default, relative paths are resolved relative to **`src/partials/uploads/`**.

```python
photo.save("avatar.jpg")        # saves to src/partials/uploads/avatar.jpg
photo.save("imgs/avatar.png")   # saves to src/partials/uploads/imgs/avatar.png
```

Features:
- Auto-creates parent directories
- Handles name conflicts: `photo.jpg` → `photo_1.jpg` → `photo_2.jpg`
- Returns the actual saved absolute path

### Accessing files

`request.files` is a dictionary of `UploadedFile` objects. You can access them using bracket notation or `.get()` (safest):

```python
# Safest: returns None if missing
photo = request.files.get("photo")

# Alternative: raises KeyError if missing
photo = request.files["photo"]

# Accessing properties (preferred)
print(photo.filename)

# Dict-style access (backward compatibility)
print(request.files["photo"]["filename"])
```

## Serving Files

Use `request.send_file()` to return a file to the browser. Relative paths are automatically resolved relative to **`src/partials/uploads/`**.

```python
def render(request):
    # Resolves to src/partials/uploads/report.pdf
    return request.send_file("report.pdf")

    # Resolves to src/partials/uploads/pdf/cv.pdf
    return request.send_file("pdf/cv.pdf")

    # Force download with custom name
    return request.send_file("data.csv", filename="export.csv")

    # Display image in browser (inline)
    return request.send_file("header.png", as_attachment=False)
```

### Path Resolution
- **Absolute paths**: Used as-is.
- **Relative paths**: Resolved relative to `src/partials/uploads`.

> [!CAUTION]
> For security, `request.send_file()` only allows serving files from within the `src/partials/uploads` directory. Attempts to access files outside this directory will return a `403 Forbidden` error.

## Configuration

You can limit the maximum upload size globally in your `Asok` app configuration:

```python
# wsgi.py
app = Asok()
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB limit
```

Default is **10 MB**. If a request exceeds this limit, Asok returns a `413 Payload Too Large` error.

---
[← Previous: API Development](20-api.md) | [Documentation](README.md) | [Next: Static Versioning →](22-static-versioning.md)
