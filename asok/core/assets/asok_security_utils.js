/**
 * ASOK Security Utilities
 * Provides security helpers for safe DOM manipulation and data validation
 *
 * SECURITY: This module provides defense-in-depth for XSS, injection, and open redirect attacks
 */

(function(window) {
  'use strict';

  const AsokSecurity = {

    /**
     * Sanitize HTML by removing dangerous elements and attributes
     * SECURITY: Prevents XSS attacks via innerHTML injection
     *
     * @param {string} html - HTML string to sanitize
     * @returns {string} - Sanitized HTML
     */
    sanitizeHtml: function(html) {
      if (typeof html !== 'string') {
        return '';
      }

      // Create a temporary div to parse HTML safely
      const temp = document.createElement('div');
      temp.innerHTML = html;

      // Remove all script tags
      const scripts = temp.querySelectorAll('script');
      scripts.forEach(s => s.remove());

      // Remove dangerous tags
      const dangerousTags = ['iframe', 'object', 'embed', 'link', 'style'];
      dangerousTags.forEach(tag => {
        const elements = temp.querySelectorAll(tag);
        elements.forEach(el => el.remove());
      });

      // Remove event handler attributes from all elements
      const allElements = temp.querySelectorAll('*');
      allElements.forEach(el => {
        // Get all attributes
        const attrs = Array.from(el.attributes);
        attrs.forEach(attr => {
          // Remove on* event handlers
          if (attr.name.toLowerCase().startsWith('on')) {
            el.removeAttribute(attr.name);
          }

          // Validate href/src attributes
          if (attr.name.toLowerCase() === 'href' || attr.name.toLowerCase() === 'src') {
            if (!this.isSafeUrl(attr.value)) {
              el.removeAttribute(attr.name);
            }
          }
        });
      });

      return temp.innerHTML;
    },

    /**
     * Validate URL is safe (blocks javascript:, data:, etc.)
     * SECURITY: Prevents open redirect and javascript protocol attacks
     *
     * @param {string} url - URL to validate
     * @returns {boolean} - True if URL is safe
     */
    isSafeUrl: function(url) {
      if (!url || typeof url !== 'string') {
        return false;
      }

      // Remove ASCII control characters (ordinals < 32 and 127) which browsers ignore/strip in URLs
      const cleanUrl = url.replace(/[\x00-\x1F\x7F]/g, '').trim().toLowerCase();

      // Block dangerous protocols
      const dangerousProtocols = [
        'javascript:',
        'data:',
        'vbscript:',
        'file:',
        'about:',
        'blob:'
      ];

      for (let i = 0; i < dangerousProtocols.length; i++) {
        if (cleanUrl.startsWith(dangerousProtocols[i])) {
          console.warn('[Asok Security] Blocked dangerous URL:', url.substring(0, 50));
          return false;
        }
      }

      // Allow relative URLs, http, https, mailto, tel
      const safeProtocolPattern = /^(https?:\/\/|mailto:|tel:|\/|#|\?)/i;

      // If URL has a protocol, it must be safe
      if (cleanUrl.indexOf(':') !== -1 && cleanUrl.indexOf(':') < 10) {
        return safeProtocolPattern.test(cleanUrl);
      }

      // Relative URLs without protocol are safe
      return true;
    },

    /**
     * Validate attribute name is safe for binding
     * SECURITY: Prevents event handler injection via attribute binding
     *
     * @param {string} attrName - Attribute name to validate
     * @returns {boolean} - True if attribute is safe to bind
     */
    isSafeAttribute: function(attrName) {
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
    safeDuration: function(duration, maxDuration) {
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
    validateWsMessage: function(data) {
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
    safeJsonParse: function(jsonString) {
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
    hasSensitiveData: function(formData) {
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
    isValidPort: function(port) {
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
    escapeHtml: function(text) {
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
