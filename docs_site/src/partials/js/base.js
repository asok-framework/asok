const toggle = document.getElementById('theme-toggle');
const sunIcon = document.getElementById('sun-icon');
const moonIcon = document.getElementById('moon-icon');
const body = document.body;

function updateIcons() {
    const isLight = body.classList.contains('light-mode');
    sunIcon.style.display = isLight ? 'block' : 'none';
    moonIcon.style.display = isLight ? 'none' : 'block';
}

// Restore theme immediately to avoid flicker
if (localStorage.getItem('asok-theme') === 'light') {
    body.classList.add('light-mode');
}
updateIcons();

toggle.addEventListener('click', () => {
    body.classList.toggle('light-mode');
    const isLight = body.classList.contains('light-mode');
    localStorage.setItem('asok-theme', isLight ? 'light' : 'dark');
    updateIcons();
});

// Search Trigger (both desktop and mobile)
const searchTriggers = [document.getElementById('search-trigger'), document.getElementById('search-trigger-mobile')];
const searchOverlay = document.getElementById('search-overlay');
const searchInput = document.getElementById('search-input');

searchTriggers.forEach(trigger => {
    if (trigger) {
        trigger.addEventListener('click', () => {
            searchOverlay.classList.add('open');
            setTimeout(() => searchInput && searchInput.focus(), 50);
        });
    }
});

// Close search overlay on background click
searchOverlay.addEventListener('click', (e) => {
    if (e.target === searchOverlay || e.target.closest('.search-hit')) {
        searchOverlay.classList.remove('open');
        if (searchInput) searchInput.value = '';
    }
});

// Mobile Menu Toggle
const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
const mobileMenu = document.getElementById('mobile-menu');
const mobileMenuOverlay = document.getElementById('mobile-menu-overlay');
const menuIconPath = document.getElementById('menu-icon-path');

if (mobileMenuToggle && mobileMenu) {
    mobileMenuToggle.addEventListener('click', () => {
        const isHidden = mobileMenu.classList.contains('hidden');
        if (isHidden) {
            mobileMenu.classList.remove('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M6 18L18 6M6 6l12 12');
            body.style.overflow = 'hidden';
        } else {
            mobileMenu.classList.add('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
            body.style.overflow = '';
        }
    });

    const mobileMenuClose = document.getElementById('mobile-menu-close');

    if (mobileMenuClose) {
        mobileMenuClose.addEventListener('click', () => {
            mobileMenu.classList.add('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
            body.style.overflow = '';
        });
    }

    // Close menu when clicking a link
    mobileMenu.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', () => {
            // Give a tiny delay for SPA if needed, but usually immediate is fine
            mobileMenu.classList.add('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
            body.style.overflow = '';
        });
    });

    if (mobileMenuOverlay) {
        mobileMenuOverlay.addEventListener('click', () => {
            mobileMenu.classList.add('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
            body.style.overflow = '';
        });
    }
}

// Handle Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (searchOverlay.classList.contains('open')) {
            searchOverlay.classList.remove('open');
        }
        if (mobileMenu && !mobileMenu.classList.contains('hidden')) {
            mobileMenu.classList.add('hidden');
            if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
            body.style.overflow = '';
        }
    }
});

// --- Asok SPA Enhancement ---
document.addEventListener('asok:success', (e) => {
    // Re-initialize code copy buttons after content swap (handled in docs.js)

    // Update active state in sidebar (now handled by docs_menu block swap)

    // Close all overlays on SPA success
    if (mobileMenu) mobileMenu.classList.add('hidden');
    if (searchOverlay) searchOverlay.classList.remove('open');
    if (menuIconPath) menuIconPath.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
    body.style.overflow = '';

    if (searchInput) searchInput.value = '';
});