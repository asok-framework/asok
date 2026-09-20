# Asok Framework Roadmap

This roadmap documents the evolution of the Asok web framework — detailing both the completed, production-ready capabilities across releases and upcoming development initiatives. Community feedback and contributions are welcome on [GitHub Discussions](https://github.com/asok-framework/asok/discussions) and [Discord](https://discord.com/invite/aYYkuPT3qR).

---

## Current Stable Release

### v0.7.0 (Released: September 2026)

**Status**: ✅ Stable

Major frontend modernization release introducing a native declarative SPA router, zero-code async form handling, and safe template expression evaluation:

**Native Declarative SPA Router:**
- **Zero-Bundler SPA Routing**: Container directive `asok-router` providing deterministic, client-side view swapping without React, Vue, or client-side JavaScript build steps.
- **Dynamic Route Matching**: `<template asok-route="/path">` with parameter extraction (`/users/:id`), query parameter tracking, and hash preservation.
- **Active Link Highlighting**: Automatic class application via `<a href="..." asok-link asok-active-class="...">` with full whitespace-separated multi-token support (`DOMTokenList` compliant).
- **Smooth Page Transitions**: Direct integration with `asok-transition` (`fade 200`, `slide-left`, `scale`) ensuring entering views stack cleanly over leaving views.
- **Route Lifecycle Hooks**: Programmatic event subscription via `window.AsokRouter.onRouteChange((route) => ...)`.
- **Dynamic Head Hydration**: Automatic title and meta tag updates on route changes (`asok-meta:title`, `asok-meta:description`).

**Zero-Code Declarative Forms:**
- **Declarative Form Dispatch**: Direct asynchronous JSON form submission via `asok-fetch-post` attribute without writing JavaScript `fetch()` calls.
- **Auto-Reset Directive**: `asok-reset` resets form inputs instantly upon successful HTTP 2xx response.
- **Built-in Reactive State Variables**: Automatically provides `loading`, `error`, and `response` (or custom name via `asok-fetch-as="result"`) states for reactive UI feedback (spinners, alerts, success banners).

**Expression Engine & Security:**
- **Safe Optional Chaining (`?.`)**: Full support for optional chaining syntax in template expressions (e.g., `result?.message`), safely navigating nested objects without raising `TypeError`.
- **Zero-Eval Strict CSP**: 100% compliant with strict Content Security Policies — no `'unsafe-eval'` or `'unsafe-inline'` required.

---

## Previous Releases (Implemented Features)

### v0.6.0 (Released: July 2026)

**Status**: ✅ Stable

Asset pipeline optimization and developer experience enhancements:
- **Optimized Asset Pipeline**: Pre-compiled and minified core runtime assets for high-throughput production serving.
- **Developer Toolbar Upgrades**: Live WebSocket connection inspector, database query timeline, and memory consumption stats.
- **Diagnostic Overlays**: Interactive error handling overlay with stack trace navigation and local variable inspection.

---

### v0.5.1 (Released: June 2026)

**Status**: ✅ Stable

Patch release — resolved table check and creation issues inside the `createsuperuser` command to automatically provision the `role_user` pivot table if missing.

---

### v0.5.0 (Released: June 2026)

**Status**: ✅ Stable

Security hardening release — full audit pass, GraphQL authentication enforcement, HMAC-signed Redis job queue, offline GraphiQL playground, and SECRET_KEY exposure fix:

**Security:**
- **GraphQL mutations blocked by default** — fail-closed authentication (`GRAPHQL_AUTHORIZE` required or `GRAPHQL_ALLOW_UNAUTHENTICATED_MUTATIONS=True`)
- **GraphQL mass assignment protection** — `protected=True` fields now correctly filtered in mutations
- **Password/hidden fields excluded from GraphQL schema** — `is_password` and `hidden` fields no longer queryable or returned
- **HMAC-signed Redis job envelopes** — background jobs are now signed with `SECRET_KEY`, unsigned jobs rejected by the worker
- **SECRET_KEY removed from WSGI environ** — no longer exposed to third-party middleware and loggers
- **Offline GraphiQL** — `asok graphql --install` downloads playground assets locally (no CDN dependency in dev)

---

### v0.4.0 (Released: June 2026)

**Status**: ✅ Stable

GraphQL api engine, community extensions system, hybrid rendering (Islands/SSG/ISR), advanced WebSocket presence/typing, multi-database query load balancing, and API versioning negotiation:

**Extensions System:**
- **Community Extensions System**: Fully extensible third-party package registration for custom pages, templates, and static assets with secure path traversal sandboxing.

**Performance & Rendering:**
- **Hybrid SSR & Hydration**: Selective/Partial hydration for islands architecture.
- **SSG & ISR**: Static Site Generation (SSG) for static routes and Incremental Static Regeneration (ISR) with automated background stale-cache warming.

**API & GraphQL:**
- **Built-in GraphQL**: Auto-generated GraphQL schema resolved from models, playground explorer in development, and subscription support.
- **API Versioning**: URL-based and header-based API versioning, negotiation headers, and deprecation sunset notifications.

**Real-Time & Scaling:**
- **Advanced WebSockets**: Real-time user presence tracking, status, Direct Messages, typing indicators, read receipts, and room authorization hooks.
- **Multi-Database Scaling**: Advanced ORM query load balancer routing queries to read replicas and write masters.

---

### v0.3.0 (Released: June 2026)

**Status**: ✅ Stable

Modern async stack, enterprise database support, and developer tooling:

**Core Framework:**
- **Async/ASGI Support**: Full async/await support with ASGI/WSGI dual engine, async middlewares, and non-blocking database queries.
- **Multi-Database Support**: PostgreSQL and MySQL backends with connection pooling, cross-engine migrations, and config-driven DB binds.
- **Redis Integration**: Native Redis support for caching, session persistence, cache warming, and fragment caching.
- **Cloud Storage**: AWS S3 file storage with automatic mime-type detection.
- **Background Jobs**: `asok worker` command for background task processing with Redis resilience.
- **Database Fixtures**: New `asok dumpdata` and `asok loaddata` CLI commands for data seeding.

**Advanced ORM:**
- **Polymorphic Relationships**: MorphTo/MorphMany for flexible model associations.
- **Self-Referencing Relationships**: Models can reference themselves (parent/child hierarchies).
- **Nested Eager Loading**: Prevent N+1 queries with deep relation loading.
- **Vector Similarity Search**: Native pgvector cosine and Euclidean distance search with `.nearest()`.

**Developer Experience:**
- **VSCode Extension**: Syntax highlighting, IntelliSense, template autocompletion, route navigation, and snippets.
- **Localization Tools**: Translation management UI and automatic string extraction.

---

### v0.2.0 (Released: June 2026)

**Status**: ✅ Stable

Enterprise web building blocks: Forms, Tables, Backups, and Scheduling:

- **Form Engine (`asok.Form`)**: ModelForm automatic generation, rich HTML5 input widgets, CSRF tokens, and comprehensive validation (`asok.Validator`).
- **Data Tables (`asok.Table`)**: Interactive datagrids with multi-column sorting, search filters, pagination, and direct-to-browser CSV streaming.
- **Task Scheduler (`asok.scheduler`)**: In-process cron and interval scheduling (`@schedule(interval="1h")`) without external daemon dependencies.
- **Disaster Recovery & Backups (`asok.backup`)**: Automated cross-engine database backups (e.g. Postgres to SQLite) and snapshot restore tools.

---

### v0.1.7 (Released: May 2026)

**Status**: ✅ Stable

Framework refactoring and architecture overhaul for long-term maintainability:
- **Module Restructuring**: Reorganized monolithic engine and CLI into clean, modular packages (`asok/core/`, `asok/orm/`, `asok/cli/`, etc.).
- **Asset Compilation**: Pre-compiled minified assets for admin, API, and developer toolbar.

---

## Upcoming Releases

### v0.8.0 - Real-Time Cluster Scaling & Streaming (Planned Q4 2026)

**Status**: 📋 Planned

- **Distributed WebSocket Adapters**: Redis Pub/Sub and Valkey broadcast adapters allowing multi-server horizontal WebSocket clustering.
- **Database Concurrency Optimizations**: Multi-process SQLite WAL connection pool queue coordination with automatic retry backoff.
- **Streaming SSR Enhancements**: HTTP/2 & HTTP/3 Early Hints (103) and chunked template streaming delivery.

---

### v1.0.0 - Enterprise Observability & Multi-Tenancy (Planned Q1 2027)

**Status**: 📋 Planned

#### Monitoring & Observability
- **Built-in OpenTelemetry & Prometheus**: Automated latency metrics collection, health check endpoints, and distributed tracing export (`/metrics`).
- **Slow Query Alerting**: Automatic warning logs and configurable notifications for database query bottlenecks.

#### Enterprise Features
- **SaaS Multi-Tenancy**: Native tenant isolation supporting dynamic schema-level and database-level tenancy.
- **CDN Integration**: Automated edge cache invalidation adapters for Cloudflare, Fastly, and CloudFront.

#### API Stability
- **Long-Term Support (LTS)**: Freezing core public APIs with strict semantic versioning and extended backward compatibility commitments.

---

## Community Input

We want to hear from you! Help shape the future of Asok:

### How to Contribute to the Roadmap

1. **Vote on Features** - React to issues with 👍 to show support
2. **Propose Features** - Open a [Feature Discussion](https://github.com/asok-framework/asok/discussions/new?category=ideas)
3. **Share Use Cases** - Tell us how you're using Asok and what you need
4. **Join Discord** - Real-time discussions on [Discord](https://discord.com/invite/aYYkuPT3qR)

---

## Release Schedule

| Version | Target Date | Status | Focus |
|---------|-------------|--------|-------|
| v0.1.4 | May 9, 2026 | ✅ Released | DX & Advanced UI |
| v0.1.6 | May 15, 2026 | ✅ Released | Security & UI Transitions |
| v0.1.7 | May 25, 2026 | ✅ Released | Architecture Overhaul |
| v0.2.0 | June 1, 2026 | ✅ Released | Forms, Data Tables, Scheduler & Backups |
| v0.3.0 | June 5, 2026 | ✅ Released | Async, Multi-DB, Redis & Vector Search |
| v0.4.0 | June 12, 2026 | ✅ Released | GraphQL, SSG/ISR & Extensions |
| v0.5.0 | June 20, 2026 | ✅ Released | Security Hardening & Fail-Closed Auth |
| v0.5.1 | June 24, 2026 | ✅ Released | CLI Fixes & Patch Update |
| v0.6.0 | July 2026 | ✅ Released | Asset Pipeline & Toolbar DX |
| v0.7.0 | September 2026 | ✅ Released | Native SPA Router & Zero-JS Forms |
| v0.8.0 | Q4 2026 | 📋 Planned | Real-Time Cluster Scaling & Streaming |
| v1.0.0 | Q1 2027 | 📋 Planned | Enterprise Observability & Multi-Tenancy (LTS) |

**Note**: Dates are approximate and subject to change based on community priorities and development capacity.

---

## Contributing to Development

Want to help build these features? Check out:

- **[Contributing Guide](CONTRIBUTING.md)** - How to contribute code
- **[Good First Issues](https://github.com/asok-framework/asok/labels/good%20first%20issue)** - Easy tasks for newcomers
- **[Discord #development](https://discord.com/invite/aYYkuPT3qR)** - Coordinate with core team

---

## Stability Promise

Asok follows [Semantic Versioning](https://semver.org/):

- **Patch releases** (0.1.x) - Bug fixes, no breaking changes
- **Minor releases** (0.x.0) - New features, backward compatible
- **Major releases** (x.0.0) - Breaking changes with migration guides

We maintain backward compatibility within major versions and provide clear upgrade paths.

---

**Last Updated**: September 20, 2026

For the most up-to-date information, check the [GitHub Projects board](https://github.com/asok-framework/asok/projects).
