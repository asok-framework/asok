/*
 * Asok Security Utilities
 * author: Asok Team
 * license: MIT
 * version: 2.0.0
*/
(function (window) {
  'use strict';

  // Allowlist of tags permitted in sanitized (untrusted) HTML. Anything else
  // (script, style, iframe, object, embed, link, form, svg, math, ...) is dropped.
  const SAFE_TAGS = new Set([
    'a', 'abbr', 'b', 'blockquote', 'br', 'caption', 'code', 'col', 'colgroup',
    'dd', 'div', 'dl', 'dt', 'em', 'figcaption', 'figure', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'hr', 'i', 'img', 'li', 'mark', 'ol', 'p', 'pre', 'q', 's', 'small',
    'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead',
    'time', 'tr', 'u', 'ul', 'wbr'
  ]);

  // Attributes whose value is a URL and must pass isSafeUrl().
  const URL_ATTRS = new Set([
    'href', 'src', 'srcset', 'action', 'formaction', 'poster', 'background',
    'cite', 'longdesc', 'xlink:href'
  ]);

  const AsokSecurity = {

    /**
     * Sanitize HTML by removing dangerous elements and attributes
     * SECURITY: Prevents XSS attacks via innerHTML injection
     *
     * @param {string} html - HTML string to sanitize
     * @returns {string} - Sanitized HTML
     */
    /**
     * Sanitize a parsed subtree in place using a tag/attribute allowlist.
     * @param {Node} root - Element or DocumentFragment to sanitize
     * @returns {Node} - The same root, sanitized
     */
    _sanitizeNode: function (root) {
      const removals = [];
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let node = walker.nextNode();
      while (node) {
        const tag = node.tagName.toLowerCase();
        if (!SAFE_TAGS.has(tag)) {
          removals.push(node);
        } else {
          Array.from(node.attributes).forEach(attr => {
            const name = attr.name.toLowerCase();
            if (name.startsWith('on') || !this.isSafeAttribute(attr.name)) {
              node.removeAttribute(attr.name);
            } else if (URL_ATTRS.has(name) && !this.isSafeUrl(attr.value)) {
              node.removeAttribute(attr.name);
            }
          });
        }
        node = walker.nextNode();
      }
      removals.forEach(n => n.remove());
      return root;
    },

    /**
     * Sanitize untrusted HTML into a DocumentFragment of safe *nodes*.
     * SECURITY: single parse into an inert <template>, no re-serialization —
     * this avoids the mutation-XSS class that affects string round-trips.
     *
     * @param {string} html - HTML string to sanitize
     * @returns {DocumentFragment} - Fragment of sanitized nodes
     */
    sanitizeToFragment: function (html) {
      const tpl = document.createElement('template');
      if (typeof html === 'string') {
        tpl.innerHTML = html;
        this._sanitizeNode(tpl.content);
      }
      return tpl.content;
    },

    /**
     * Sanitize HTML and return a string. Prefer sanitizeToFragment() when
     * inserting into the DOM, to avoid a second parse of the returned string.
     *
     * @param {string} html - HTML string to sanitize
     * @returns {string} - Sanitized HTML
     */
    sanitizeHtml: function (html) {
      if (typeof html !== 'string') {
        return '';
      }
      const container = document.createElement('div');
      container.appendChild(this.sanitizeToFragment(html));
      return container.innerHTML;
    },

    /**
     * Validate URL is safe (blocks javascript:, data:, etc.)
     * SECURITY: Prevents open redirect and javascript protocol attacks
     *
     * @param {string} url - URL to validate
     * @returns {boolean} - True if URL is safe
     */
    isSafeUrl: function (url) {
      if (!url || typeof url !== 'string') {
        return false;
      }

      // SECURITY: decode percent-encoding and strip control chars before checks
      // (defeats e.g. j%61vascript: and embedded newline/tab obfuscation).
      let cleanUrl;
      try {
        cleanUrl = decodeURIComponent(url).replace(/[\x00-\x1F\x7F]/g, '').trim();
      } catch (e) {
        cleanUrl = url.replace(/[\x00-\x1F\x7F]/g, '').trim();
      }

      // Parse an explicit scheme per RFC 3986:
      //   scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
      const schemeMatch = cleanUrl.toLowerCase().match(/^([a-z][a-z0-9+.-]*):/);
      if (schemeMatch) {
        // Allowlist of schemes; everything else (javascript:, data:, vbscript:,
        // blob:, file:, about:, ...) is rejected regardless of length.
        const allowedSchemes = ['http', 'https', 'mailto', 'tel'];
        if (allowedSchemes.indexOf(schemeMatch[1]) === -1) {
          console.warn('[Asok Security] Blocked URL scheme:', schemeMatch[1]);
          return false;
        }
        return true;
      }

      // No explicit scheme → relative path, fragment (#), or query (?). Safe.
      return true;
    },

    /**
     * Validate attribute name is safe for binding
     * SECURITY: Prevents event handler injection via attribute binding
     *
     * @param {string} attrName - Attribute name to validate
     * @returns {boolean} - True if attribute is safe to bind
     */
    isSafeAttribute: function (attrName) {
      if (!attrName || typeof attrName !== 'string') {
        return false;
      }

      const attrLower = attrName.toLowerCase();

      // Block event handlers
      if (attrLower.startsWith('on')) {
        console.warn('[Asok Security] Blocked event handler attribute:', attrName);
        return false;
      }

      // Dangerous attributes that could execute code
      const dangerousAttrs = [
        'srcdoc',
        'formaction',
        'data-bind',
        'xmlns:xlink'
      ];

      if (dangerousAttrs.indexOf(attrLower) !== -1) {
        console.warn('[Asok Security] Blocked dangerous attribute:', attrName);
        return false;
      }

      return true;
    },

    /**
     * Validate and cap duration values for setTimeout
     * SECURITY: Prevents timing-based DoS attacks
     *
     * @param {number} duration - Duration in milliseconds
     * @param {number} maxDuration - Maximum allowed duration (default 10000ms)
     * @returns {number} - Safe duration value
     */
    safeDuration: function (duration, maxDuration) {
      maxDuration = maxDuration || 10000; // 10 seconds max by default

      const parsed = parseInt(duration, 10);
      if (isNaN(parsed) || parsed < 0) {
        return 0;
      }

      return Math.min(parsed, maxDuration);
    },

    /**
     * Validate WebSocket message structure
     * SECURITY: Prevents injection attacks via malformed messages
     *
     * @param {object} data - Parsed WebSocket message
     * @returns {boolean} - True if message structure is valid
     */
    validateWsMessage: function (data) {
      if (!data || typeof data !== 'object') {
        return false;
      }

      // Must have an operation
      if (!data.op || typeof data.op !== 'string') {
        return false;
      }

      // Validate operation types
      const validOps = ['render', 'model_event', 'broadcast', 'reload'];
      if (validOps.indexOf(data.op) === -1) {
        console.warn('[Asok Security] Unknown WebSocket operation:', data.op);
        return false;
      }

      // Validate component ID format if present
      if (data.cid) {
        if (typeof data.cid !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(data.cid)) {
          console.warn('[Asok Security] Invalid component ID format:', data.cid);
          return false;
        }
      }

      // Validate HTML content if present
      if (data.html !== undefined && typeof data.html !== 'string') {
        console.warn('[Asok Security] Invalid HTML type in message');
        return false;
      }

      return true;
    },

    /**
     * Safe JSON parsing with error handling
     * SECURITY: Prevents DoS via malformed JSON
     *
     * @param {string} jsonString - JSON string to parse
     * @returns {object|null} - Parsed object or null if invalid
     */
    safeJsonParse: function (jsonString) {
      try {
        return JSON.parse(jsonString);
      } catch (error) {
        console.error('[Asok Security] JSON parse error:', error.message);
        return null;
      }
    },

    /**
     * Detect sensitive form fields that should not be in GET requests
     * SECURITY: Prevents sensitive data leakage in URLs
     *
     * @param {FormData} formData - Form data to check
     * @returns {boolean} - True if sensitive data is present
     */
    hasSensitiveData: function (formData) {
      const sensitiveFields = [
        'password', 'passwd', 'pwd',
        'token', 'csrf', 'csrf_token',
        'secret', 'api_key', 'apikey',
        'authorization', 'auth',
        'credit_card', 'card_number', 'cvv',
        'ssn', 'social_security'
      ];

      for (const pair of formData.entries()) {
        const keyLower = pair[0].toLowerCase();
        for (let i = 0; i < sensitiveFields.length; i++) {
          if (keyLower.indexOf(sensitiveFields[i]) !== -1) {
            return true;
          }
        }
      }

      return false;
    },

    /**
     * Validate WebSocket port configuration
     * SECURITY: Prevents port hijacking in development mode
     *
     * @param {number} port - Port number to validate
     * @returns {boolean} - True if port is valid
     */
    isValidPort: function (port) {
      const parsed = parseInt(port, 10);

      // Port must be a valid number
      if (isNaN(parsed)) {
        return false;
      }

      // Port must be in valid range (avoid privileged ports in production)
      if (parsed < 1024 || parsed > 65535) {
        console.warn('[Asok Security] Invalid port range:', port);
        return false;
      }

      return true;
    },

    /**
     * Escape HTML entities for safe text insertion
     * SECURITY: Prevents XSS when inserting user data as text
     *
     * @param {string} text - Text to escape
     * @returns {string} - Escaped text
     */
    escapeHtml: function (text) {
      if (typeof text !== 'string') {
        return '';
      }

      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }
  };

  // Export to window
  window.AsokSecurity = AsokSecurity;

})(window);
