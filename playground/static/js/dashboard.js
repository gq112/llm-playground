import ObservabilityModule from './modules/observability.js?v=20260714-throughput';

const targetForm = document.getElementById('target-form');
const targetUrl = document.getElementById('target-url');
const targetApiKey = document.getElementById('target-api-key');
const savedSources = document.getElementById('target-saved-sources');
const targetStatus = document.getElementById('target-status');
const themeToggle = document.getElementById('theme-toggle');
const SAVED_SOURCES_KEY = 'observability-saved-sources';

function getSavedSources() {
    try {
        const saved = JSON.parse(localStorage.getItem(SAVED_SOURCES_KEY) || '[]');
        return Array.isArray(saved)
            ? saved.filter((url) => typeof url === 'string' && /^https?:\/\//i.test(url))
            : [];
    } catch {
        return [];
    }
}

function renderSavedSources(selected = '') {
    if (!savedSources) return;
    const sources = getSavedSources();
    savedSources.replaceChildren(new Option('已保存服务', ''));
    sources.forEach((url) => savedSources.add(new Option(url, url)));
    savedSources.value = sources.includes(selected) ? selected : '';
}

function rememberSource(url) {
    const sources = [url, ...getSavedSources().filter((saved) => saved !== url)].slice(0, 12);
    localStorage.setItem(SAVED_SOURCES_KEY, JSON.stringify(sources));
    renderSavedSources(url);
}

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
        renderSavedSources(target.url || '');
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
        rememberSource(result.url);
        setStatus(`Polling ${result.url}/metrics`, 'connected');
    } catch (error) {
        setStatus(error.message, 'error');
    } finally {
        button.disabled = false;
    }
});

savedSources.addEventListener('change', () => {
    if (!savedSources.value) return;
    targetUrl.value = savedSources.value;
    targetApiKey.value = '';
    targetForm.requestSubmit();
});

targetUrl.addEventListener('input', () => {
    if (savedSources.value && savedSources.value !== targetUrl.value.trim()) savedSources.value = '';
});

themeToggle.addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});

const savedTheme = localStorage.getItem('dashboard-theme');
applyTheme(savedTheme || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));

await ObservabilityModule.loadTemplate();
ObservabilityModule.onViewActivated();
renderSavedSources();
loadTarget();
