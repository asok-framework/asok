# Advanced Authentication

Asok provides built-in, zero-dependency tools for modern authentication methods like Magic Links and OAuth2 (Google, GitHub, etc.).

## 1. Magic Links (Passwordless)

Magic Links allow users to log in securely using only their email address, without needing to remember a password.

### Implementation
Access the Magic Link helper via `request.auth.magic`.

#### Sending a Link
In your login page action:
```python
from asok import Request

def post(request: Request):
    email = request.form.get("email")
    if email:
        request.auth.magic.send(request, email)
        return "Check your email!"
```

#### Callback Handling
Create a page at `/auth/magic/callback.py`:
```python
from asok import Request

def get(request: Request):
    token = request.query.get("token")
    email = request.auth.magic.verify_token(request, token)
    
    if email:
        user = User.find(email=email)
        if not user:
            user = User.create(email=email)
        request.login(user)
        return request.redirect("/")
    
    return "Invalid or expired token."
```

## 2. OAuth2 (Social Login)

Asok includes a standard-library implementation for OAuth2 providers.

### Supported Providers
- **Google**
- **GitHub**

### Configuration
You will need to pass your Client ID and Client Secret from the provider dashboard.

#### Step 1: Redirect to Provider
```python
from asok import Request

def get(request: Request):
    auth_url = request.auth.oauth.get_auth_url(
        provider_name="google",
        client_id="YOUR_CLIENT_ID",
        redirect_uri="https://myapp.com/auth/google/callback",
        state="optional-random-state"
    )
    return request.redirect(auth_url)
```

#### Step 2: Handle Callback
```python
from asok import Request

def get(request: Request):
    code = request.query.get("code")
    user_info = request.auth.oauth.callback(
        provider_name="google",
        client_id="YOUR_CLIENT_ID",
        client_secret="YOUR_CLIENT_SECRET",
        code=code,
        redirect_uri="https://myapp.com/auth/google/callback"
    )
    
    # user_info contains: email, name, picture, provider_id
    user = User.find(email=user_info["email"])
    if not user:
        user = User.create(email=user_info["email"], name=user_info["name"])
    
    request.login(user)
    return request.redirect("/")
```

## 3. API Tokens (Bearer Auth)

Asok supports stateless API authentication via Bearer tokens. This is ideal for mobile apps or external integrations.

### Usage
Tokens are signed via the framework's `SECRET_KEY`, making them stateless and naturally secure.

#### Generating a Token
```python
from asok import Request

def get(request: Request):
    # Generate a permanent token for the current user
    token = request.auth.token.create(request, request.user.id)
    
    # Or generate a temporary token (e.g. 24 hours)
    token = request.auth.token.create(request, request.user.id, expires_in=86400)
    
    return request.api({"token": token})
```

#### Accessing your API
Clients should send the token in the `Authorization` header:
`Authorization: Bearer <your-token>`

Asok's `request.user` will **automatically detect** the token and authenticate the request.

```python
# page/api/profile.py
from asok import Request

def get(request):
    if not request.user:
        return request.api_error("Unauthorized", status=401)
        
    return request.api({"email": request.user.email})
```

## 4. Security Notes
- **SECRET_KEY**: All tokens are signed using the `SECRET_KEY` configured in your `.env`. Never share this key.
- **Expiration**: Magic Links expire in 1 hour by default.
- **HTTPS**: OAuth2 requires HTTPS for redirect URIs in production.

---
[← Previous: HTML Streaming](31-html-streaming.md) | [Documentation](README.md) | [Next: Advanced Deployment →](33-deployment.md)
