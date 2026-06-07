# Asok Framework Roadmap

This roadmap outlines the planned features and improvements for upcoming Asok releases. Community feedback is welcome - join the discussion on [GitHub Discussions](https://github.com/asok-framework/asok/discussions) or [Discord](https://discord.com/invite/aYYkuPT3qR).

---

## Current Stable Release

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

## Previous Releases

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
- **Vector Similarity Search**: Built-in support for pgvector search.

**Developer Experience:**
- **VSCode Extension**: Syntax highlighting, IntelliSense, template autocompletion, route navigation, and snippets.
- **Localization Tools**: Translation management UI and automatic string extraction.

---

### v0.1.7 (Released: May 2026)

**Status**: ✅ Stable

Framework refactoring and architecture overhaul for long-term maintainability:
- **Module Restructuring**: Reorganized monolithic engine and CLI into clean, modular packages (`asok/core/`, `asok/orm/`, `asok/cli/`, etc.).
- **Asset Compilation**: Pre-compiled minified assets for admin, API, and developer toolbar.

---

## Upcoming Releases

### v0.5.0 - Enterprise Scale & Observability (Planned Q1 2027)

**Status**: 📋 Planned

#### Monitoring & Observability
- **Built-in Monitoring** - Request/response logging, performance metrics collection, health check endpoints, and Prometheus/Grafana integrations.
- **Slow Query Alerting** - Automatic warning logs and mail alerts for database bottlenecks.

#### Enterprise Features
- **Multi-Tenancy** - Native SaaS multi-tenant schema isolation.
- **CDN Caching** - Built-in asset pipeline delivery optimization integration.

#### Developer Experience
- **Performance Profiling Tools** - CLI flame graphs and memory usage profilers.

---

## Long-term Vision (2027+)

### v0.5.0 and Beyond

These features are under consideration based on community feedback:

- **Microservices Support** - Service mesh integration, gRPC support
- **CDN Integration** - Automatic asset optimization and delivery
- **Multi-tenancy** - SaaS application support with tenant isolation
- **Advanced Caching** - Redis integration, cache warming, fragment caching
- **AI/ML Integration** - Built-in AI utilities (RAG pipelines, inference APIs, vector search)
- **Mobile Backend** - Push notifications, mobile-specific APIs
- **Testing Framework** - Enhanced testing utilities, browser automation
- **Localization Tools** - Translation management UI, automatic string extraction

---

## Community Input

We want to hear from you! Help shape the future of Asok:

### How to Contribute to the Roadmap

1. **Vote on Features** - React to issues with 👍 to show support
2. **Propose Features** - Open a [Feature Discussion](https://github.com/asok-framework/asok/discussions/new?category=ideas)
3. **Share Use Cases** - Tell us how you're using Asok and what you need
4. **Join Discord** - Real-time discussions on [Discord](https://discord.com/invite/aYYkuPT3qR)

### Current Polls

Check [GitHub Discussions](https://github.com/asok-framework/asok/discussions) for active polls on:
- Database priorities (PostgreSQL vs MySQL vs MongoDB)
- Admin UI feature requests
- Plugin ecosystem priorities
- IDE integration priorities (VSCode, PyCharm, Sublime Text)

---

## Release Schedule

| Version | Target Date | Status | Focus |
|---------|-------------|--------|-------|
| v0.1.4 | May 9, 2026 | ✅ Released | DX & Advanced UI |
| v0.1.6 | May 15, 2026 | ✅ Released | Security & UI Transitions |
| v0.1.7 | May 25, 2026 | ✅ Released | Architecture Overhaul |
| v0.3.0 | June 1, 2026 | ✅ Released | Async & Multi-DB Support |
| v0.4.0 | June 7, 2026 | ✅ Released | GraphQL & Extensions |
| v0.5.0 | Q1 2027 | 📋 Planned | Enterprise Scaling & Monitoring |

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

**Last Updated**: June 7, 2026

For the most up-to-date information, check the [GitHub Projects board](https://github.com/asok-framework/asok/projects).
