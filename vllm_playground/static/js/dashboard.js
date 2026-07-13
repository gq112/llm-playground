import ObservabilityModule from './modules/observability.js';

const targetForm = document.getElementById('target-form');
const targetUrl = document.getElementById('target-url');
const targetApiKey = document.getElementById('target-api-key');
const targetStatus = document.getElementById('target-status');
const themeToggle = document.getElementById('theme-toggle');

function setStatus(message, state = '') {
    targetStatus.textContent = message;
    targetStatus.className = `target-status ${state}`;
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('dashboard-theme', theme);
    themeToggle.textContent = theme === 'dark' ? '☼' : '◐';
}

async function loadTarget() {
    try {
        const response = await fetch('/api/observability/target');
        if (!response.ok) throw new Error('Could not load source settings');
        const target = await response.json();
        targetUrl.value = target.url || '';
        if (target.url) setStatus(`Polling ${target.url}/metrics`, 'connected');
    } catch {
        setStatus('Source settings unavailable', 'error');
    }
}

targetForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const url = targetUrl.value.trim();
    const button = targetForm.querySelector('button[type="submit"]');
    button.disabled = true;
    setStatus('Connecting…');
    try {
        const response = await fetch('/api/observability/target', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: targetApiKey.value || null }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Unable to save source');
        targetApiKey.value = '';
        setStatus(`Polling ${result.url}/metrics`, 'connected');
    } catch (error) {
        setStatus(error.message, 'error');
    } finally {
        button.disabled = false;
    }
});

themeToggle.addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});

const savedTheme = localStorage.getItem('dashboard-theme');
applyTheme(savedTheme || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));

await ObservabilityModule.loadTemplate();
ObservabilityModule.onViewActivated();
loadTarget();
