# Changelog

All notable changes to the Asok Framework will be documented in this file.

## [0.2.0] - 2026-04-19
### Added
- **Search System**: Full-text search engine based on SQLite FTS5 with real-time indexing.
- **Middleware API**: New declarative middleware system with support for global and route-specific execution.
- **Hot Reload**: Advanced hot-reload capability for local development.

### Fixed
- Improved nonce handling for CSP compatibility.
- Resolved minor routing conflicts with trailing slashes.

## [0.1.1] - 2026-04-18
### Changed
- Refined CSS variables in `base.html` for better dark/light mode consistency.
- Optimized documentation indexing for faster search results.

### Fixed
- Fix 404 error page not displaying correctly on nested docs paths.
- Removed redundant navigation links from Markdown files in the browser view.

## [0.1.0] - 2026-04-17
### Added
- **Core Engine**: Zero-dependency robust WSGI core.
- **Routing**: Modern file-based routing system (`src/pages/`) with dynamic parameter support.
- **AsokDB**: Built-in, zero-config SQLite ORM with relations and automatic hashing.
- **Templates**: Jinja-like template engine with inheritance, includes, and extensive filters.
- **Security**: Built-in CSRF protection, secure cookie sessions, and automatic HTML escaping.
- **Real-time**: Native WebSocket support (Alive Engine).
- **Reactive Components**: Live components integration.
- **Admin Panel**: Auto-generated, highly customizable back-office interface.
- **CLI**: Fully featured `asok` command line tool (`create`, `dev`, `make`).
- **Tests**: 100% test coverage with comprehensive unit and integration suites.
