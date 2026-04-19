# Security Audit Report: Asok Framework

**Date**: 2026-04-16  
**Auditor**: Antigravity (Security Expert Mode)  
**Scope**: `asok/` core source code (ORM, Templates, Routing, WebSockets, Admin)

---

## 1. Executive Summary

Asok demonstrates a high degree of "Security by Design." For a framework built entirely on the Python standard library with zero dependencies, it successfully implements robust protections against the OWASP Top 10 vulnerabilities. 

Recent hardening measures have successfully neutralized a critical XSS vector in JSON serialization and strengthened CSRF protections for secure environments.

**Overall Security Rating**: 🟢 **Strong** (Production Ready)

---

## 2. Vulnerability Analysis

### 2.1 SQL Injection (SQLi)
*   **Mechanism**: The ORM (`orm.py`) uses parameter binding (`?` placeholders) for all user-controllable values.
*   **Safeguards**: Column names and table names used in query building are whitelisted against the model's `_fields` list.
*   **Findings**: **Safe**. No direct string interpolation of user data into SQL queries was found.

### 2.2 Cross-Site Scripting (XSS)
*   **Mechanism**: Templates (`templates.py`) use a custom compiler that auto-escapes all `{{ }}` expressions.
*   **Hardening**: The `tojson` and `dump` filters implement **Unicode Escaping** (via `html_safe_json`) for HTML-sensitive characters: `<` becomes `\u003c`, `>` becomes `\u003e`, and `&` becomes `\u0026`. This prevents attackers from breaking out of `<script>` tags when injecting JSON data.
*   **Findings**: **Strongly Protected**. Both HTML and JSON contexts are handled correctly.

### 2.3 Cross-Site Request Forgery (CSRF)
*   **Mechanism**: `core.py` and `request.py` implement a robust token-based defense. Tokens are HMAC-signed and required for all state-changing methods.
*   **Hardening**: For HTTPS connections, Asok implements **Strict Origin Verification**. Requests must provide an `Origin` or `Referer` header that matches the expected `Host`. This prevents Cross-Site attacks even in the event of partial token disclosure.
*   **Findings**: **Safe**. Protection meets enterprise security standards.

### 2.4 Path Traversal
*   **Mechanism**: `request.py` (`send_file`) and `templates.py` (`_safe_resolve`) use `os.path.abspath` and `startswith()` checks to ensure file access is restricted to the project's root or `uploads/` directory.
*   **Findings**: **Safe**.

### 2.5 Session & Cookie Security
*   **Mechanism**: Cookies use `HttpOnly` and `SameSite=Lax` by default. In production mode (`DEBUG=False`), the `Secure` flag is automatically appended.
*   **Findings**: **Safe**. Session IDs are HMAC-signed to prevent tampering.

### 2.6 Password Hashing
*   **Mechanism**: Uses `PBKDF2-HMAC-SHA256` with 100,000 iterations and a cryptographically secure random salt.
*   **Findings**: **Solid**. Matches industry standards (OWASP/NIST).

### 2.7 Cross-Site WebSocket Hijacking (CSWH)
*   **Mechanism**: The `WebSocketServer` (`ws.py`) authenticates users via cookies.
*   **Risk**: **MEDIUM**. Does not yet validate the `Origin` header.
*   **Recommendation**: Validate the `Origin` header against a whitelist.

---

## 3. Admin Security Review

The built-in Admin interface (`asok/admin/`) includes several enterprise-grade security features:
1.  **Granular Permissions**: Supports model-level verbs (`view`, `add`, `edit`, `delete`, `export`).
2.  **2FA (TOTP)**: Built-in support for Google Authenticator / Authy.
3.  **Login Rate Limiting**: IP-based brute-force protection.
4.  **Audit Logs**: Full activity tracking for all administrative actions.

---

## 4. Hardening Status

| Component | Status | Protection Implemented |
|---|---|---|
| **JSON Serialization** | ✅ **Hardened** | Unicode escaping for `<` and `>` tags. |
| **CSRF Logic** | ✅ **Hardened** | Origin/Referer matching for HTTPS. |
| **Path Traversal** | ✅ **Verified** | Strict filesystem boundary checks. |
| **WebSockets** | ⚠️ **Pending** | Needs `Origin` check in `ws.py`. |

---

## 5. Conclusion

Following the 2026 security audit and subsequent hardening phase, Asok represents a highly secure environment for modern web applications. The framework's architecture effectively mitigates major web vulnerability classes through safe-by-default design choices.

---
[← Previous: Optimization](30-optimization.md) | [Documentation](README.md) | [Next: HTML Streaming →](31-html-streaming.md)
