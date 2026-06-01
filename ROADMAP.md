# Asok Framework Roadmap

This roadmap outlines the planned features and improvements for upcoming Asok releases. Community feedback is welcome - join the discussion on [GitHub Discussions](https://github.com/asok-framework/asok/discussions) or [Discord](https://discord.com/invite/aYYkuPT3qR).

---

## Current Stable Release

### v0.3.0 (Released: June 2026)

**Status**: ✅ Stable

Modern async stack, enterprise database support, and developer tooling:

**Core Framework:**
- **Async/ASGI Support**: Full async/await support with ASGI/WSGI dual engine, async middlewares, and non-blocking database queries
- **Multi-Database Support**: PostgreSQL and MySQL backends with connection pooling, cross-engine migrations, and config-driven DB binds
- **Redis Integration**: Native Redis support for caching, session persistence, cache warming, and fragment caching
- **Cloud Storage**: AWS S3 file storage with automatic mime-type detection
- **Background Jobs**: `asok worker` command for background task processing with Redis resilience
- **Database Fixtures**: New `asok dumpdata` and `asok loaddata` CLI commands for data seeding

**Advanced ORM:**
- **Polymorphic Relationships**: MorphTo/MorphMany for flexible model associations
- **Self-Referencing Relationships**: Models can reference themselves (parent/child hierarchies)
- **Nested Eager Loading**: Prevent N+1 queries with deep relation loading
- **Custom Relationship Types**: Extensible relationship system
- **Vector Similarity Search**: Built-in support for embedding-based search (PostgreSQL pgvector)
- **Query Optimization Tools**: N+1 detection in development, query plan analysis, automatic index suggestions, slow query logging

**Real-Time Features:**
- **WebSocket Rooms**: Room-based broadcasting with join/leave for multi-user collaboration

**Admin Panel Enhancements:**
- **Inline Editing**: Quick updates without full page navigation
- **Advanced Filtering**: Date ranges, multi-field filters, saved filter presets
- **Column Customization**: Toggle column visibility for personalized views

**Developer Experience:**
- **VSCode Extension**: Official IDE integration with syntax highlighting, IntelliSense, template autocompletion, route navigation, and snippets
- **Localization Tools**: Translation management UI and automatic string extraction for i18n
- **Query Debugging**: Built-in tools for identifying and fixing performance issues

[View Full Changelog](https://github.com/asok-framework/asok-docs/blob/main/CHANGELOG.md)

---

## Previous Releases

### v0.1.7 (Released: May 2026)

**Status**: ✅ Stable

Framework refactoring and architecture overhaul for long-term maintainability:
- **Module Restructuring**: Reorganized monolithic engine and CLI into clean, modular packages (`asok/core/`, `asok/orm/`, `asok/cli/`, etc.) with 100% backward compatibility.
- **Asset Compilation**: Pre-compiled minified assets for admin, API, and developer toolbar. Added official Python 3.13 support.
- **Enhanced Test Coverage**: Added dedicated suites for AJAX CSRF rotation, SPA reactivity fixes, developer toolbar, and API static files.

---

## Upcoming Releases

### v0.4.0 - GraphQL & Enterprise Scale (Q4 2026)

**Status**: 📋 Planned

#### API & GraphQL

- **GraphQL Support** - Modern API development
  - Built-in GraphQL server
  - Auto-generated schema from models
  - Query complexity analysis
  - GraphQL playground in development
  - Subscriptions via WebSockets

- **API Versioning** - Professional API management
  - URL-based versioning (/api/v1/, /api/v2/)
  - Header-based versioning
  - API deprecation warnings and sunset headers
  - Version negotiation and content-type versioning

#### Enterprise & Scalability

- **Advanced WebSocket Features** - Enhanced real-time capabilities
  - User presence tracking and status updates
  - Room permissions and authentication
  - Private messaging and direct messages
  - Typing indicators and read receipts

- **Multi-Database Scaling** - Horizontal scaling
  - Read replicas configuration
  - Sharding strategies for large datasets
  - Multi-region database support
  - Automatic read/write load balancing

#### Developer Experience

- **Plugin System** - Extend Asok with third-party packages
  - Plugin discovery and auto-registration
  - Hook system for core events
  - Plugin configuration API
  - Official plugin registry

- **CLI Enhancements** - Advanced developer tools
  - Performance profiling tools (flame graphs, memory usage)
  - Database introspection commands (show schema, explain queries)
  - Asset pipeline optimization (automatic sprite generation)
  - Environment management (config validation, secrets vault)

- **VSCode Extension Enhancements** - Advanced IDE features
  - Debug configuration templates
  - Live preview for templates
  - Integrated test runner
  - Visual database schema browser

#### Performance & Rendering

- **Advanced SSR & Hydration** - Enhanced rendering strategies
  - Hybrid rendering (SSR + Client-side hydration)
  - Selective/Partial hydration for islands architecture
  - Static site generation (SSG) for marketing pages
  - Incremental static regeneration (ISR)

#### Monitoring & Observability

- **Built-in Monitoring** - Production-ready observability
  - Request/response logging
  - Performance metrics (response time, query count)
  - Error tracking and alerts
  - Health check endpoints
  - Integration with Prometheus/Grafana

#### Admin Interface

- **Admin Dashboard Enhancements** - Advanced administration features
  - Drag-and-drop file uploads with progress
  - Dashboard widgets and statistics
  - Batch operations interface
  - Advanced export/import tools

---

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
| v0.4.0 | Q4 2026 | 📋 Planned | Advanced Features |
| v0.5.0 | Q2 2027 | 💭 Conceptual | Enterprise Scale |

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

**Last Updated**: June 1, 2026

For the most up-to-date information, check the [GitHub Projects board](https://github.com/asok-framework/asok/projects).
