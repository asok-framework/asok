# Asok Framework Roadmap

This roadmap outlines the planned features and improvements for upcoming Asok releases. Community feedback is welcome - join the discussion on [GitHub Discussions](https://github.com/asok-framework/asok/discussions) or [Discord](https://discord.com/invite/aYYkuPT3qR).

---

## Current Stable Release

### v0.1.3 (Released: May 2026)

**Status**: ✅ Stable

Latest release with admin error pages, enhanced template engine, comprehensive security audit, and validation improvements.

[View Full Changelog](https://github.com/asok-framework/asok-docs/blob/main/CHANGELOG.md)

---

## Upcoming Releases

### v0.2.0 - Enterprise Features (Q2 2026)

**Status**: 🚧 In Planning

#### Database & ORM

- **PostgreSQL & MySQL Support** - Multi-database backend support beyond SQLite
  - PostgreSQL support with JSONB, Arrays, and advanced types
  - MySQL/MariaDB support with full compatibility
  - Connection pooling and transaction management
  - Migration compatibility layer
  - Database switching via configuration
  - Unified query builder across all databases

- **Advanced Relationships** - Enhanced ORM capabilities
  - Polymorphic relationships (morphTo/morphMany)
  - Self-referencing relationships
  - Nested eager loading optimization
  - Query scopes and global scopes

#### Real-time & Background Jobs

- **WebSocket Rooms** - Multi-user real-time collaboration
  - Room-based message broadcasting
  - User presence tracking
  - Room permissions and authentication
  - Private messaging support

- **Job Queue System** - Background task processing
  - Redis/SQLite-based queue backends
  - Delayed job execution
  - Job retry with exponential backoff
  - Job status monitoring and logging
  - Priority queues

#### Developer Experience

- **Plugin System** - Extend Asok with third-party packages
  - Plugin discovery and auto-registration
  - Hook system for core events
  - Plugin configuration API
  - Official plugin registry

- **CLI Enhancements** - Improved developer tools
  - Performance profiling tools (flame graphs, memory usage)
  - Database introspection commands (show schema, explain queries)
  - Asset pipeline optimization (automatic sprite generation)
  - Environment management (config validation, secrets vault)

- **VSCode Extension** - Official IDE integration
  - Syntax highlighting for Asok templates
  - IntelliSense for template tags and filters
  - Model field autocompletion
  - Route navigation and URL reverse lookup
  - Built-in snippets for common patterns
  - Debug configuration templates
  - Live preview for templates

#### Admin Interface

- **Admin UI Improvements** - Enhanced administration experience
  - Inline editing for quick updates
  - Drag-and-drop file uploads with progress
  - Advanced filtering with date ranges
  - Column visibility customization
  - Saved filter presets
  - Dashboard widgets and statistics

---

### v0.3.0 - Modern Stack (Q3 2026)

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

#### Performance & Scalability

- **Advanced SSR & Hydration** - Enhanced rendering strategies
  - Hybrid rendering (SSR + Client-side hydration)
  - Selective/Partial hydration for islands architecture
  - Static site generation (SSG) for marketing pages
  - Incremental static regeneration (ISR)

- **Multi-Database Support** - Horizontal scaling
  - Read replicas configuration
  - Sharding strategies
  - Connection pooling per database
  - Load balancing

- **Async/Await Support** - ASGI compatibility
  - Full async request handling
  - Async ORM queries
  - Async middleware support
  - WebSocket async handlers

#### Monitoring & Observability

- **Built-in Monitoring** - Production-ready observability
  - Request/response logging
  - Performance metrics (response time, query count)
  - Error tracking and alerts
  - Health check endpoints
  - Integration with Prometheus/Grafana

- **Query Optimization** - Automatic performance tuning
  - N+1 query detection in development
  - Query plan analysis
  - Automatic index suggestions
  - Slow query logging

---

## Long-term Vision (2027+)

### v0.4.0 and Beyond

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
| v0.1.3 | May 2026 | ✅ Released | Security & Templates |
| v0.2.0 | June 2026 | 🚧 In Progress | Enterprise Features |
| v0.3.0 | September 2026 | 📋 Planned | Modern Stack |
| v0.4.0 | Q1 2027 | 💭 Conceptual | Advanced Features |

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

**Last Updated**: May 3, 2026

For the most up-to-date information, check the [GitHub Projects board](https://github.com/asok-framework/asok/projects).
