// --- Global Utilities ---
window.addCopyButtons = () => {
    document.querySelectorAll('.codehilite').forEach(block => {
        if (block.querySelector('.copy-btn')) return;
        
        const button = document.createElement('button');
        button.className = 'copy-btn';
        button.setAttribute('aria-label', 'Copy code');
        button.innerHTML = `
            <svg width="14" height="14" fill="none" stroke="white" stroke-width="2.5" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
            </svg>
        `;
        
        button.addEventListener('click', async () => {
            const pre = block.querySelector('pre');
            if (!pre) return;
            const code = pre.innerText;
            await navigator.clipboard.writeText(code);
            
            button.innerHTML = `
                <svg width="14" height="14" fill="none" stroke="#818cf8" stroke-width="3" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path>
                </svg>
            `;
            button.classList.add('copied');
            
            setTimeout(() => {
                button.innerHTML = `
                    <svg width="14" height="14" fill="none" stroke="white" stroke-width="2.5" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                    </svg>
                `;
                button.classList.remove('copied');
            }, 2000);
        });
        
        block.appendChild(button);
    });
};

// Handle SPA success events
document.addEventListener('asok:success', () => {
    window.addCopyButtons();
    setTimeout(window.addCopyButtons, 100);
});

document.addEventListener('DOMContentLoaded', () => {
    // Initialize copy buttons
    window.addCopyButtons();
    
    // Auto-initialize when DOM changes (for SPA support)
    const observer = new MutationObserver((mutations) => {
        // Debounce or at least check if we really need to run
        window.addCopyButtons();
    });
    
    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
    }

    // --- Search Command Palette ---
    const overlay = document.getElementById('search-overlay');
    const searchInput = document.getElementById('search-input');
    const trigger = document.getElementById('search-trigger');
    const resultsBox = document.getElementById('search-results');

    if (overlay && searchInput) {
        const open = () => {
            overlay.classList.add('open');
            setTimeout(() => searchInput.focus(), 50);
        };
        const close = () => {
            overlay.classList.remove('open');
            searchInput.blur();
        };

        if (trigger) trigger.addEventListener('click', open);

        // Keyboard nav inside results
        let activeIdx = -1;
        const getItems = () => resultsBox.querySelectorAll('a.search-hit');
        const highlight = (idx) => {
            const items = getItems();
            items.forEach(el => el.classList.remove('active'));
            if (idx >= 0 && idx < items.length) {
                items[idx].classList.add('active');
                items[idx].scrollIntoView({ block: 'nearest' });
            }
        };

        const obs = new MutationObserver(() => { activeIdx = -1; });
        obs.observe(resultsBox, { childList: true, subtree: true });

        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                overlay.classList.contains('open') ? close() : open();
            }
            if (!overlay.classList.contains('open')) return;

            if (e.key === 'Escape') { close(); return; }

            const items = getItems();
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                activeIdx = Math.min(activeIdx + 1, items.length - 1);
                highlight(activeIdx);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                activeIdx = Math.max(activeIdx - 1, 0);
                highlight(activeIdx);
            } else if (e.key === 'Enter' && activeIdx >= 0 && activeIdx < items.length) {
                e.preventDefault();
                items[activeIdx].click();
            }
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });
    }
});
