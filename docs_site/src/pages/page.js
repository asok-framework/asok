// Copy pip install command
document.getElementById('copy-pip-btn').addEventListener('click', function () {
    navigator.clipboard.writeText('pip install asok').then(() => {
        const copyIcon = this.querySelector('.copy-icon');
        const checkIcon = this.querySelector('.check-icon');

        copyIcon.classList.add('hidden');
        checkIcon.classList.remove('hidden');

        setTimeout(() => {
            copyIcon.classList.remove('hidden');
            checkIcon.classList.add('hidden');
        }, 2000);
    });
});

// Playground tabs
document.querySelectorAll('.playground-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const targetTab = tab.dataset.tab;

        // Update tabs
        document.querySelectorAll('.playground-tab').forEach(t => {
            t.classList.remove('active', 'font-bold');
            t.classList.add('font-medium');
            t.style.background = 'transparent';
            t.style.color = 'var(--editor-tab-inactive)';
            t.style.border = '1px solid transparent';
        });

        tab.classList.add('active', 'font-bold');
        tab.classList.remove('font-medium');
        tab.style.background = 'var(--editor-tab-active-bg)';
        tab.style.color = 'var(--accent-primary)';
        tab.style.border = '1px solid var(--editor-tab-active-border)';

        // Update content
        document.querySelectorAll('.playground-content').forEach(content => {
            content.classList.add('hidden');
        });

        document.querySelector(`[data-content="${targetTab}"]`).classList.remove('hidden');
    });
});