console.error(
  "ASOK ERROR: Reactive directives detected but CSP unsafe-eval is disabled!\n" +
    "Directives (asok-state, asok-text, asok-on:*) will NOT work.\n\n" +
    "Fix: Add CSP_UNSAFE_EVAL=true to your .env file, then restart."
);
