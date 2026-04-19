# Native Vector Search

Asok is one of the few frameworks to offer **native Vector Search** without any external dependencies or extensions. This makes it a perfect fit for building AI-powered features like semantic search, recommendation engines, and RAG (Retrieval-Augmented Generation) applications.

## 1. Defining a Vector Field

To store embeddings (vectors), use the `Field.Vector` type. You must specify the number of dimensions.

```python
class Article(Model):
    title = Field.String()
    content = Field.Text()
    # 1536 is a common dimension for OpenAI embeddings
    embedding = Field.Vector(dimensions=1536)
```

## 2. Storing Vectors

Asok handles the complex task of serializing your Python lists into compact binary blobs for you.

```python
# Just pass a standard list of floats
embedding_data = [0.1, -0.2, 0.45, ...] 

Article.create(
    title="Hello Vector",
    embedding=embedding_data
)
```

## 3. Querying with `.nearest()`

The `.nearest()` method allows you to find the most similar rows based on a target vector. It uses optimized SQL-level calculations.

### Cosine Similarity (Recommended for AI)
This measures the angle between vectors. Perfect for semantic search.

```python
query_vector = [0.05, 0.1, ...]

# Find the 5 most similar articles
results = Article.query().nearest("embedding", query_vector).limit(5).get()
```

### Euclidean Distance
Measures the straight-line distance between points.

```python
# Find the physically closest items
results = Article.query().nearest("embedding", target, metric="euclidean").get()
```

## 4. How it works

Asok injects custom mathematics functions directly into the SQLite engine. 
- **Cosine Similarity**: Returns a score between -1 and 1 (where 1 is identical).
- **Euclidean Distance**: Returns the geometric distance.

Because these calculations happen inside the database engine, they are extremely efficient for thousands of rows, all while maintaining the "Zero Dependency" promise.

---
[← Previous: Advanced Forms](35-forms-advanced.md) | [Documentation](README.md) | [Next: Reactive Components →](37-reactive-components.md)
