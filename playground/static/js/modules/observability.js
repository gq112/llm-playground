/**
 * Observability Module -- full-page metrics dashboard.
 *
 * The dashboard loads the metrics view into a small page placeholder.
 */

import {
    METRIC_REGISTRY,
    CATEGORIES,
    formatMetricValue,
    getThresholdStatus,
    groupByCategory,
} from './metrics-registry.js';
import { metricsPoller } from './metrics-poller.js';

function displayModelName(modelName = '') {
    const segments = modelName.trim().replace(/\/+$/, '').split('/').filter(Boolean);
    return segments[segments.length - 1] || modelName;
}

class LocalLineChart {
    constructor(options, data, target) {
        this.options = options;
        this.data = data;
        this.target = target;
        this.canvas = document.createElement('canvas');
        this.canvas.className = 'local-line-chart';
        this.tooltip = document.createElement('div');
        this.tooltip.className = 'local-line-chart-tooltip';
        this._onPointerMove = this._handlePointerMove.bind(this);
        this._onPointerLeave = this._handlePointerLeave.bind(this);
        target.classList.add('local-line-chart-host');
        target.replaceChildren(this.canvas, this.tooltip);
        this.canvas.addEventListener('mousemove', this._onPointerMove);
        this.canvas.addEventListener('mouseleave', this._onPointerLeave);
        this._render();
    }

    setData(data) {
        this.data = data;
        this._render();
    }

    destroy() {
        this.canvas.removeEventListener('mousemove', this._onPointerMove);
        this.canvas.removeEventListener('mouseleave', this._onPointerLeave);
        this.canvas.remove();
        this.tooltip.remove();
    }

    _render() {
        const { width, height, series } = this.options;
        const ratio = window.devicePixelRatio || 1;
        const canvas = this.canvas;
        canvas.width = Math.max(1, Math.floor(width * ratio));
        canvas.height = Math.max(1, Math.floor(height * ratio));
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;

        const ctx = canvas.getContext('2d');
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);

        const [timestamps = [], ...values] = this.data;
        const numbers = values.flat().filter(Number.isFinite);
        if (!timestamps.length || !numbers.length) {
            this._geometry = null;
            ctx.fillStyle = '#9eacc4';
            ctx.font = '13px system-ui, sans-serif';
            ctx.fillText('No numeric data to chart', 16, 28);
            return;
        }

        const padding = { top: 12, right: 14, bottom: 28, left: 52 };
        const plotWidth = Math.max(1, width - padding.left - padding.right);
        const plotHeight = Math.max(1, height - padding.top - padding.bottom);
        const xMin = timestamps[0];
        const lastTimestamp = timestamps[timestamps.length - 1];
        const xMax = lastTimestamp > xMin ? lastTimestamp : xMin + 60;
        const rawMin = Math.min(...numbers);
        const rawMax = Math.max(...numbers);
        const spread = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.1, 1);
        const yMin = rawMin - spread * 0.08;
        const yMax = rawMax + spread * 0.08;
        const x = (value) => padding.left + ((value - xMin) / (xMax - xMin || 1)) * plotWidth;
        const y = (value) => padding.top + (1 - (value - yMin) / (yMax - yMin || 1)) * plotHeight;
        this._geometry = { padding, plotWidth, width, timestamps, x, y };

        ctx.font = '11px system-ui, sans-serif';
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.18)';
        ctx.fillStyle = '#9eacc4';
        ctx.lineWidth = 1;
        for (let step = 0; step <= 4; step++) {
            const yPos = padding.top + (plotHeight * step) / 4;
            ctx.beginPath();
            ctx.moveTo(padding.left, yPos);
            ctx.lineTo(width - padding.right, yPos);
            ctx.stroke();
            const label = (yMax - ((yMax - yMin) * step) / 4).toPrecision(3);
            ctx.fillText(label, 3, yPos + 4);
        }

        const minuteTicks = [];
        let previousMinute = null;
        timestamps.forEach((timestamp) => {
            const minute = Math.floor(timestamp / 60);
            if (minute !== previousMinute) {
                minuteTicks.push(timestamp);
                previousMinute = minute;
            }
        });
        if (minuteTicks.length === 1 && xMax > xMin) minuteTicks.push(xMax);
        ctx.textAlign = 'center';
        minuteTicks.forEach((tick) => {
            const xPos = x(tick);
            ctx.beginPath();
            ctx.moveTo(xPos, padding.top);
            ctx.lineTo(xPos, height - padding.bottom);
            ctx.stroke();
            const label = new Date(tick * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            ctx.fillText(label, xPos, height - 7);
        });
        ctx.textAlign = 'start';

        values.forEach((row, index) => {
            ctx.strokeStyle = series[index + 1]?.stroke || '#648cff';
            ctx.lineWidth = series[index + 1]?.width || 1;
            ctx.beginPath();
            const points = [];
            let drawing = false;
            row.forEach((value, pointIndex) => {
                if (!Number.isFinite(value)) {
                    drawing = false;
                    return;
                }
                const xPos = x(timestamps[pointIndex]);
                const yPos = y(value);
                points.push([xPos, yPos]);
                if (drawing) ctx.lineTo(xPos, yPos);
                else ctx.moveTo(xPos, yPos);
                drawing = true;
            });
            ctx.stroke();
            ctx.fillStyle = series[index + 1]?.stroke || '#648cff';
            points.forEach(([xPos, yPos]) => {
                ctx.beginPath();
                ctx.arc(xPos, yPos, 2.25, 0, Math.PI * 2);
                ctx.fill();
            });
        });
    }

    _handlePointerMove(event) {
        if (!this._geometry) return;
        const rect = this.canvas.getBoundingClientRect();
        const localX = event.clientX - rect.left;
        const { padding, plotWidth, timestamps, width } = this._geometry;
        const ratio = Math.min(1, Math.max(0, (localX - padding.left) / plotWidth));
        const index = Math.round(ratio * (timestamps.length - 1));
        if (index !== this._hoverIndex) {
            this._hoverIndex = index;
            this._render();
            this._drawHover(index);
        }

        const timestamp = new Date(timestamps[index] * 1000).toLocaleString();
        const escape = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
        })[char]);
        const rows = this.data.slice(1).map((series, seriesIndex) => {
            const value = series[index];
            if (!Number.isFinite(value)) return '';
            const label = this.options.series[seriesIndex + 1]?.label || `指标 ${seriesIndex + 1}`;
            const formatter = this.options.tooltip?.formatter;
            const display = formatter ? formatter(value, seriesIndex) : value.toPrecision(5);
            return `<div><span>${escape(label)}</span><strong>${escape(display)}</strong></div>`;
        }).join('');
        const title = this.options.tooltip?.title || '指标详情';
        const modelName = this.options.tooltip?.modelName;
        this.tooltip.innerHTML = `<b>${escape(title)}</b><time>${escape(timestamp)}</time>${modelName ? `<em>模型：${escape(modelName)}</em>` : ''}${rows}`;
        const rawModelName = this.options.tooltip?.modelName || '';
        const shortModelName = displayModelName(rawModelName);
        const visibleSeries = this.data.slice(1)
            .map((series, seriesIndex) => ({
                value: series[index],
                label: this.options.series[seriesIndex + 1]?.label || `指标 ${seriesIndex + 1}`,
                seriesIndex,
            }))
            .filter(({ value }) => Number.isFinite(value));
        const compactValues = visibleSeries.map(({ value, label, seriesIndex }) => {
            const formatter = this.options.tooltip?.formatter;
            const display = formatter ? formatter(value, seriesIndex) : value.toPrecision(5);
            return visibleSeries.length > 1
                ? `<div><span>${escape(label)}</span><strong>${escape(display)}</strong></div>`
                : `<strong class="local-line-chart-tooltip-value">${escape(display)}</strong>`;
        }).join('');
        const modelMarkup = rawModelName
            ? `<span class="local-line-chart-tooltip-model" title="${escape(rawModelName)}">${escape(shortModelName)}</span>`
            : '';
        this.tooltip.innerHTML = `${modelMarkup}${compactValues}`;
        this.tooltip.style.display = 'block';
        this.tooltip.style.left = `${Math.min(Math.max(localX + 12, 6), width - 190)}px`;
        this.tooltip.style.top = `${Math.max(event.clientY - rect.top + 10, 6)}px`;
    }

    _drawHover(index) {
        if (!this._geometry) return;
        const { width, padding, timestamps, x, y } = this._geometry;
        const ratio = window.devicePixelRatio || 1;
        const ctx = this.canvas.getContext('2d');
        const xPos = x(timestamps[index]);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.75)';
        ctx.beginPath();
        ctx.moveTo(xPos, padding.top);
        ctx.lineTo(xPos, this.options.height - padding.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        this.data.slice(1).forEach((series, seriesIndex) => {
            const value = series[index];
            if (!Number.isFinite(value)) return;
            ctx.fillStyle = this.options.series[seriesIndex + 1]?.stroke || '#648cff';
            ctx.beginPath();
            ctx.arc(xPos, y(value), 3.5, 0, Math.PI * 2);
            ctx.fill();
        });
        ctx.restore();
    }

    _handlePointerLeave() {
        this._hoverIndex = null;
        this.tooltip.style.display = 'none';
        this._render();
    }
}

function chartRenderData(data, pointsPerMinute = 5) {
    const [timestamps = [], ...series] = data;
    if (timestamps.length <= pointsPerMinute) return data;

    const selected = [];
    let lastSlot = null;
    timestamps.forEach((timestamp, index) => {
        const minute = Math.floor(timestamp / 60);
        const slot = minute * pointsPerMinute + Math.min(
            pointsPerMinute - 1,
            Math.floor(((timestamp - minute * 60) / 60) * pointsPerMinute),
        );
        const hasValue = series.some((values) => Number.isFinite(values[index]));
        if (slot !== lastSlot) {
            selected.push({ index, hasValue });
            lastSlot = slot;
        } else if (hasValue || !selected[selected.length - 1].hasValue) {
            selected[selected.length - 1] = { index, hasValue };
        }
    });

    const indexes = selected.map(({ index }) => index);
    return [
        indexes.map((index) => timestamps[index]),
        ...series.map((values) => indexes.map((index) => values[index])),
    ];
}

function createLineChart(options, data, target) {
    const renderData = chartRenderData(data);
    return window.uPlot ? new window.uPlot(options, renderData, target) : new LocalLineChart(options, renderData, target);
}

const ObservabilityModule = {
    templateLoaded: false,
    _unsubscribe: null,
    _currentTab: 'overview',
    _sortColumn: 'name',
    _sortAsc: true,
    _searchFilter: '',
    _uplotChart: null,
    _tsSeconds: 300,
    _tsSelectedMetrics: new Set(),
    _tsHistory: [],
    _alertedMetrics: new Set(),
    _alertHistory: [],
    _customThresholds: null,
    _lastScrapeLocalRef: null,
    _prevScrapeAge: null,
    _liveHistory: [],
    _liveCharts: [],
    _liveFetchInProgress: false,
    _currentModelName: '',
    _currentDraftModelName: '',

    // -- Template loading ---------------------------------------------------

    async loadTemplate() {
        const container = document.getElementById('observability-view');
        if (!container) {
            console.error('Observability view container not found');
            return;
        }

        if (this.templateLoaded && container.querySelector('.obs-layout')) {
            return;
        }

        try {
            const response = await fetch('/static/templates/observability.html');
            if (!response.ok) throw new Error(`Failed to load template: ${response.status}`);

            const html = await response.text();
            container.innerHTML = html;
            this.templateLoaded = true;

            this._loadAlertThresholds();
            this._bindEvents();
            console.log('Observability template loaded');
        } catch (error) {
            console.error('Failed to load observability template:', error);
            container.innerHTML = `
                <div class="obs-no-data">
                    <h3>Failed to load Observability</h3>
                    <p>${error.message}</p>
                    <button class="obs-btn obs-btn-primary" onclick="window.ObservabilityModule.loadTemplate()">Retry</button>
                </div>
            `;
        }
    },

    onViewActivated() {
        if (!this.templateLoaded) {
            this.loadTemplate();
        }

        if (!this._unsubscribe) {
            this._unsubscribe = metricsPoller.subscribe((data) => this._onMetrics(data));
        }

        if (!metricsPoller._timer) {
            metricsPoller.start();
        }

    },

    onViewDeactivated() {
        // Keep polling -- other consumers (sidebar badge) may need it
    },

    // -- Internal ----------------------------------------------------------

    _bindEvents() {
        const tabs = document.getElementById('obs-tabs');
        if (tabs) {
            tabs.addEventListener('click', (e) => {
                const btn = e.target.closest('.obs-tab');
                if (!btn) return;
                this._switchTab(btn.dataset.obsTab);
            });
        }

        const search = document.getElementById('obs-search');
        if (search) {
            search.addEventListener('input', () => {
                this._searchFilter = search.value.toLowerCase();
                this._renderAllMetricsTable();
            });
        }

        const sortHeaders = document.querySelectorAll('#obs-metrics-table th[data-sort]');
        sortHeaders.forEach((th) => {
            th.addEventListener('click', () => {
                const col = th.dataset.sort;
                if (this._sortColumn === col) {
                    this._sortAsc = !this._sortAsc;
                } else {
                    this._sortColumn = col;
                    this._sortAsc = true;
                }
                this._updateSortArrows();
                this._renderAllMetricsTable();
            });
        });

        const demoBtn = document.getElementById('obs-demo-btn');
        if (demoBtn) demoBtn.addEventListener('click', () => this._runDemo());

        const clearBtn = document.getElementById('obs-clear-btn');
        if (clearBtn) clearBtn.addEventListener('click', () => this._clearDemo());

        const exportBtn = document.getElementById('obs-export-btn');
        if (exportBtn) exportBtn.addEventListener('click', () => this._exportJSON());

        const exportTableBtn = document.getElementById('obs-export-table-btn');
        if (exportTableBtn) exportTableBtn.addEventListener('click', () => this._exportCSV());

        // Time Series controls
        document.querySelectorAll('.obs-ts-range').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.obs-ts-range').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                this._tsSeconds = parseInt(btn.dataset.seconds, 10);
                const customVal = document.getElementById('obs-ts-custom-val');
                if (customVal) customVal.value = '';
                this._loadTimeSeries();
            });
        });

        // Custom range input
        const customGoBtn = document.getElementById('obs-ts-custom-go');
        const customValInput = document.getElementById('obs-ts-custom-val');
        const customUnitSelect = document.getElementById('obs-ts-custom-unit');
        const applyCustomRange = () => {
            if (!customValInput || !customUnitSelect) return;
            const val = parseInt(customValInput.value, 10);
            if (!val || val < 1) return;
            const multiplier = customUnitSelect.value === 'm' ? 60 : 1;
            this._tsSeconds = val * multiplier;
            document.querySelectorAll('.obs-ts-range').forEach((b) => b.classList.remove('active'));
            this._loadTimeSeries();
        };
        if (customGoBtn) customGoBtn.addEventListener('click', applyCustomRange);
        if (customValInput) customValInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') applyCustomRange();
        });

        // "Back to live" button
        const backToLiveBtn = document.getElementById('obs-ts-back-to-live');
        if (backToLiveBtn) backToLiveBtn.addEventListener('click', () => {
            this._tsSeconds = 300;
            document.querySelectorAll('.obs-ts-range').forEach((b) => b.classList.remove('active'));
            const defaultBtn = document.querySelector('.obs-ts-range[data-seconds="300"]');
            if (defaultBtn) defaultBtn.classList.add('active');
            if (customValInput) customValInput.value = '';
            this._loadTimeSeries();
        });

        const exportTsBtn = document.getElementById('obs-export-ts-btn');
        if (exportTsBtn) exportTsBtn.addEventListener('click', () => this._exportTimeSeries());

        // Latency export
        const exportLatBtn = document.getElementById('obs-export-latency-btn');
        if (exportLatBtn) exportLatBtn.addEventListener('click', () => this._exportLatency());

        // Alert settings
        const alertSettingsBtn = document.getElementById('obs-alerts-settings-btn');
        if (alertSettingsBtn) alertSettingsBtn.addEventListener('click', () => this._showAlertSettings());
    },

    _switchTab(tabId) {
        this._currentTab = tabId;
        document.querySelectorAll('.obs-tab').forEach((t) => {
            t.classList.toggle('active', t.dataset.obsTab === tabId);
        });
        document.querySelectorAll('.obs-tab-content').forEach((c) => {
            c.classList.remove('active');
        });
        const target = document.getElementById(`obs-tab-${tabId}`);
        if (target) target.classList.add('active');

        if (tabId === 'time-series') {
            this._initTsPicker();
            this._loadTimeSeries();
        }
        if (tabId === 'latency' && this._latestMetrics) {
            this._renderLatency(this._latestMetrics);
        }
    },

    _onMetrics({ all }) {
        const source = (all && all.source) || 'none';
        this._updateDemoButtons(source);
        this._updateBackendBadge(all?.backend);

        if (!all || !all.metrics) {
            this._clearUnavailableMetrics();
            this._showNoData(true, all);
            return;
        }
        const metrics = all.metrics;
        if (Object.keys(metrics).length === 0) {
            this._clearUnavailableMetrics();
            this._showNoData(true, all);
            return;
        }
        this._showNoData(false, all);
        this._latestMetrics = metrics;
        this._latestBackend = all.backend;
        this._updateCurrentModelName(metrics);

        const ageEl = document.getElementById('obs-scrape-age');
        if (ageEl && all.scrape_age_seconds != null) {
            const serverAge = all.scrape_age_seconds;
            if (this._prevScrapeAge === null || serverAge < this._prevScrapeAge - 1) {
                this._lastScrapeLocalRef = Date.now() - serverAge * 1000;
            }
            this._prevScrapeAge = serverAge;
            const localAge = ((Date.now() - this._lastScrapeLocalRef) / 1000).toFixed(1);
            ageEl.textContent = `Last scrape: ${localAge}s ago`;
        }

        this._renderOverview(metrics);
        this._renderLiveInference(metrics, all);
        this._renderAllMetricsTable();
        this._renderAlerts(metrics);

        if (this._currentTab === 'time-series') {
            this._initTsPicker();
        }
        if (this._currentTab === 'time-series' && this._uplotChart) {
            this._appendLivePoint(metrics);
        }
        if (this._currentTab === 'latency') {
            this._renderLatency(metrics);
        }
    },

    _clearUnavailableMetrics() {
        this._latestMetrics = null;
        this._latestBackend = null;
        this._liveDescriptors = [];
        this._liveHistory = [];
        this._liveCharts.forEach((chart) => chart.destroy());
        this._liveCharts = [];
        const liveCharts = document.getElementById('obs-live-charts');
        if (liveCharts) liveCharts.replaceChildren();
    },

    _updateBackendBadge(backend) {
        const badge = document.getElementById('obs-backend-badge');
        if (!badge) return;
        const names = { vllm: 'vLLM', sglang: 'SGLang', demo: 'Demo' };
        const name = names[backend];
        badge.textContent = name || '';
        badge.classList.toggle('visible', Boolean(name));
    },

    _showNoData(show, allData) {
        const nd = document.getElementById('obs-overview-no-data');
        const remoteNd = document.getElementById('obs-remote-no-data');
        const isRemoteNoMetrics = allData?.run_mode === 'remote' && allData?.source === 'none';
        const livePanel = document.getElementById('obs-live-inference');
        if (livePanel && show) livePanel.style.display = 'none';

        if (show && isRemoteNoMetrics) {
            if (nd) nd.style.display = 'none';
            if (remoteNd) remoteNd.style.display = 'block';
        } else if (show) {
            if (nd) nd.style.display = 'block';
            if (remoteNd) remoteNd.style.display = 'none';
        } else {
            if (nd) nd.style.display = 'none';
            if (remoteNd) remoteNd.style.display = 'none';
        }
    },

    // -- Live inference cockpit -------------------------------------------

    _liveMetricDescriptors(backend, metrics) {
        const isSglang = backend === 'sglang';
        const prefix = isSglang ? 'sglang:' : 'vllm:';
        const specs = isSglang ? [
            ['token_usage', 'Token Usage (%)', 'percent'],
            ['num_used_tokens', 'Token Usage (tokens)', 'integer', 'tokens'],
            ['num_queue_reqs', 'Queued Requests', 'integer'],
            ['gen_throughput', 'Generation Throughput', 'number', 'tok/s'],
            ['observability:prompt_token_rate', 'Input Token Rate', 'number', 'tok/s'],
            ['observability:generation_token_rate', 'Output Token Rate', 'number', 'tok/s'],
            ['observability:total_token_rate', 'Total Token Rate', 'number', 'tok/s'],
            ['num_running_reqs', 'Running Requests', 'integer'],
            ['cache_hit_rate', 'Cache Hit Rate', 'percent'],
            ['spec_num_steps', 'Speculative Steps', 'integer'],
            ['spec_num_draft_tokens', 'Draft Tokens / Step', 'integer'],
            ['time_to_first_token_seconds', 'TTFT Avg', 'duration_ms', null, 'avg'],
            ['time_to_first_token_seconds', 'TTFT P90', 'duration_ms', null, 'p90'],
            ['time_to_first_token_seconds', 'TTFT P99', 'duration_ms', null, 'p99'],
            ['time_per_output_token_seconds', 'TPOT Avg', 'duration_ms', null, 'avg'],
            ['time_per_output_token_seconds', 'TPOT P90', 'duration_ms', null, 'p90'],
            ['time_per_output_token_seconds', 'TPOT P99', 'duration_ms', null, 'p99'],
            ['e2e_request_latency_seconds', 'E2E Latency Avg', 'duration_ms', null, 'avg'],
            ['e2e_request_latency_seconds', 'E2E Latency P90', 'duration_ms', null, 'p90'],
            ['e2e_request_latency_seconds', 'E2E Latency P99', 'duration_ms', null, 'p99'],
            ['per_stage_req_latency_seconds', 'Stage Latency Avg', 'duration_ms', null, 'avg'],
            ['per_stage_req_latency_seconds', 'Stage Latency P90', 'duration_ms', null, 'p90'],
            ['per_stage_req_latency_seconds', 'Stage Latency P99', 'duration_ms', null, 'p99'],
        ] : [
            ['kv_cache_usage_perc', 'Cache Usage', 'percent'],
            ['num_requests_waiting', 'Waiting Requests', 'integer'],
            ['avg_generation_throughput_toks_per_s', 'Generation Throughput', 'number', 'tok/s'],
            ['observability:prompt_token_rate', 'Input Token Rate', 'number', 'tok/s'],
            ['observability:generation_token_rate', 'Output Token Rate', 'number', 'tok/s'],
            ['observability:total_token_rate', 'Total Token Rate', 'number', 'tok/s'],
            ['num_requests_running', 'Running Requests', 'integer'],
            ['prefix_cache_hit_rate', 'Prefix Cache Hit Rate', 'percent'],
            ['observability:spec_acceptance_rate', 'Draft Acceptance Rate', 'percent'],
            ['observability:spec_mean_accept_length', 'Mean Accepted Length', 'number', 'tok/draft'],
            ['observability:spec_draft_token_rate', 'Draft Token Rate', 'number', 'tok/s'],
            ['observability:spec_accepted_token_rate', 'Accepted Token Rate', 'number', 'tok/s'],
            ['time_to_first_token_seconds', 'TTFT Avg', 'duration_ms', null, 'avg'],
            ['time_to_first_token_seconds', 'TTFT P90', 'duration_ms', null, 'p90'],
            ['time_to_first_token_seconds', 'TTFT P99', 'duration_ms', null, 'p99'],
            ['request_time_per_output_token_seconds', 'TPOT Avg', 'duration_ms', null, 'avg'],
            ['request_time_per_output_token_seconds', 'TPOT P90', 'duration_ms', null, 'p90'],
            ['request_time_per_output_token_seconds', 'TPOT P99', 'duration_ms', null, 'p99'],
            ['e2e_request_latency_seconds', 'E2E Latency Avg', 'duration_ms', null, 'avg'],
            ['e2e_request_latency_seconds', 'E2E Latency P90', 'duration_ms', null, 'p90'],
            ['e2e_request_latency_seconds', 'E2E Latency P99', 'duration_ms', null, 'p99'],
        ];

        const descriptors = specs.map(([name, label, format, unit, percentile]) => {
            const key = name.includes(':') ? name : `${prefix}${name}`;
            return { key, label, format, unit, percentile, historyKey: percentile ? `${key}::${percentile}` : key };
        }).filter(({ key }) => metrics[key]);
        if (isSglang) {
            const acceptedLengthKey = [
                'sglang:spec_accept_length', 'sglang:spec_accept_len', 'sglang:accept_length',
            ].find((key) => metrics[key]);
            if (acceptedLengthKey) {
                descriptors.push({
                    key: acceptedLengthKey,
                    label: 'Accepted Length',
                    format: 'number',
                    unit: 'tok/draft',
                    historyKey: acceptedLengthKey,
                });
            }
        }
        return descriptors;
    },

    _liveValue(descriptor, entry) {
        if (!entry) return null;
        return descriptor.percentile ? entry[descriptor.percentile] : (entry.value ?? entry.p50 ?? null);
    },

    _renderLiveInference(metrics, all) {
        const panel = document.getElementById('obs-live-inference');
        if (!panel) return;
        const descriptors = this._liveMetricDescriptors(all?.backend, metrics);
        panel.style.display = descriptors.length ? '' : 'none';
        if (!descriptors.length) return;

        this._liveDescriptors = descriptors;
        const interval = all?.scrape_interval_seconds;
        const frequency = document.getElementById('obs-live-frequency');
        if (frequency) frequency.textContent = interval ? `${interval} 秒采样` : '实时采样';
        this._renderLiveStats(metrics);
        this._refreshLiveHistory();
    },

    async _refreshLiveHistory() {
        if (this._liveFetchInProgress) return;
        this._liveFetchInProgress = true;
        try {
            this._liveHistory = await metricsPoller.getHistory(null, 300);
            if (this._latestMetrics && this._liveDescriptors?.length) {
                this._renderLiveStats(this._latestMetrics);
                this._buildLiveCharts();
            }
        } catch {
            // The current statistics remain useful if the history query fails.
        } finally {
            this._liveFetchInProgress = false;
        }
    },

    _renderLiveStats(metrics) {
        const container = document.getElementById('obs-live-stats');
        if (!container) return;
        const prefix = this._latestBackend === 'sglang' ? 'sglang:' : 'vllm:';
        const cards = [
            {
                label: 'Cumulative Requests',
                keys: [`${prefix}num_requests`, `${prefix}requests`, `${prefix}request_success`],
                format: 'integer',
                note: 'service process total',
                color: '#60a5fa',
            },
            {
                label: 'Cumulative Input Tokens',
                keys: [`${prefix}prompt_tokens`],
                format: 'integer',
                note: 'service process total',
                color: '#a78bfa',
            },
            {
                label: 'Cumulative Output Tokens',
                keys: [`${prefix}generation_tokens`],
                format: 'integer',
                note: 'service process total',
                color: '#34d399',
            },
            {
                label: 'Decode Throughput',
                keys: ['observability:generation_token_rate'],
                format: 'number',
                unit: 'tok/s',
                note: 'output tokens / sample interval',
                color: '#f97316',
            },
        ].map((card) => {
            const key = card.keys.find((candidate) => metrics[candidate]?.value != null);
            return key ? { ...card, value: metrics[key].value } : null;
        }).filter(Boolean);

        container.style.display = cards.length ? '' : 'none';
        container.innerHTML = cards.map((card) => `<article class="obs-live-stat" style="--stat-accent:${card.color}">
            <span class="obs-live-stat-label">${this._escapeHtml(card.label)}</span>
            <strong class="obs-live-stat-current">${formatMetricValue(card.value, card.format, card.unit)}</strong>
            <span class="obs-live-stat-note">${this._escapeHtml(card.note)}</span>
        </article>`).join('');
    },

    _rankLiveDescriptors(descriptors, limit) {
        const priority = [
            'KV Usage', 'KV Cache Usage', 'Queued Requests', 'Waiting Requests',
            'Generation Throughput', 'Input Token Rate', 'Output Token Rate',
            'Total Token Rate', 'Running Requests', 'Radix Cache Hit Rate',
            'Prefix Cache Hit Rate', 'Draft Acceptance Rate', 'TTFT Avg', 'TTFT P90', 'TTFT P99',
            'TPOT Avg', 'TPOT P90', 'TPOT P99', 'E2E Latency Avg', 'E2E Latency P90', 'E2E Latency P99',
            'Stage Latency Avg', 'Stage Latency P90', 'Stage Latency P99',
        ];
        return [...descriptors]
            .sort((a, b) => {
                const aRank = priority.indexOf(a.label);
                const bRank = priority.indexOf(b.label);
                return (aRank < 0 ? priority.length : aRank) - (bRank < 0 ? priority.length : bRank);
            })
            .slice(0, limit);
    },

    _overviewStatDescriptors(descriptors) {
        const labels = [
            'TTFT Avg', 'TTFT P90', 'TTFT P99',
            'TPOT Avg', 'TPOT P90', 'TPOT P99',
            'E2E Latency Avg', 'E2E Latency P90', 'E2E Latency P99',
            'Stage Latency Avg', 'Stage Latency P90', 'Stage Latency P99',
            'Output Token Rate', 'Input Token Rate',
            'Running Requests', 'Waiting Requests', 'Queued Requests',
            'Mean Accepted Length', 'Accepted Length', 'Token Usage (tokens)', 'Token Usage (%)', 'Cache Usage',
        ];
        return labels
            .map((label) => descriptors.find((descriptor) => descriptor.label === label))
            .filter(Boolean);
    },

    _liveWindowStats(descriptor) {
        const now = Date.now();
        const fiveMinute = this._liveHistory
            .map((point) => point[descriptor.historyKey])
            .filter((value) => Number.isFinite(value));
        const samples = this._liveHistory
            .filter((point) => now - new Date(point.timestamp).getTime() <= 60_000)
            .map((point) => point[descriptor.historyKey])
            .filter((value) => Number.isFinite(value));
        return {
            average: samples.length ? samples.reduce((sum, value) => sum + value, 0) / samples.length : null,
            peak: fiveMinute.length ? Math.max(...fiveMinute) : null,
        };
    },

    _labelValueFromLabels(labels = '', keys = []) {
        for (const key of keys) {
            const match = labels.match(new RegExp(`(?:^|,)\\s*${key}="([^"]+)"`));
            if (match) return match[1];
        }
        return '';
    },

    _modelNameFromLabels(labels = '') {
        return this._labelValueFromLabels(labels, ['model_name', 'served_model_name']);
    },

    _draftModelNameFromLabels(labels = '') {
        return this._labelValueFromLabels(labels, [
            'draft_model_name', 'draft_model', 'draft_model_path',
            'speculative_draft_model', 'speculative_draft_model_name',
        ]);
    },

    _displayModelName(modelName = '') {
        return displayModelName(modelName);
    },

    _updateCurrentModelName(metrics) {
        const entries = Object.values(metrics);
        const modelName = entries
            .map((entry) => this._modelNameFromLabels(entry?.labels))
            .find(Boolean);
        const draftModelName = entries
            .map((entry) => this._draftModelNameFromLabels(entry?.labels))
            .find(Boolean);
        if (modelName) this._currentModelName = modelName;
        if (draftModelName) this._currentDraftModelName = draftModelName;

        const element = document.getElementById('obs-model-name');
        const displayName = this._displayModelName(this._currentModelName);
        if (element) {
            element.textContent = displayName ? `模型：${displayName}` : '';
            element.title = this._currentModelName;
            element.classList.toggle('visible', Boolean(displayName));
        }

        const draftElement = document.getElementById('obs-draft-model-name');
        const draftDisplayName = this._displayModelName(this._currentDraftModelName);
        if (!draftElement) return;
        draftElement.textContent = draftDisplayName ? `草稿模型：${draftDisplayName}` : '';
        draftElement.title = this._currentDraftModelName;
        draftElement.classList.toggle('visible', Boolean(draftDisplayName));
    },

    _buildLiveCharts() {
        const container = document.getElementById('obs-live-charts');
        if (!container || !this._liveDescriptors) return;
        this._liveCharts.forEach((chart) => chart.destroy());
        this._liveCharts = [];

        const chartMetrics = this._overviewStatDescriptors(this._liveDescriptors)
            .filter((descriptor) => this._liveHistory.some((point) => Number.isFinite(point[descriptor.historyKey])));
        container.innerHTML = chartMetrics.map((descriptor, index) =>
            `<div class="obs-live-chart"><span class="obs-live-chart-title">${this._escapeHtml(descriptor.label)} · last 5 min</span><div id="obs-live-chart-${index}"></div></div>`
        ).join('');
        const colors = ['#60a5fa', '#34d399', '#f59e0b', '#a78bfa', '#f472b6', '#22d3ee', '#fb7185', '#84cc16'];
        if (!chartMetrics.length) {
            container.innerHTML = '<div class="obs-live-chart-empty">正在积累实时趋势数据…</div>';
            return;
        }
        const percentileLabel = (descriptor) => descriptor.label.match(/(Avg|P90|P99)$/)?.[1] || descriptor.label;
        const latencyGroups = new Map();
        const singleMetricCharts = [];
        chartMetrics.forEach((descriptor) => {
            if (descriptor.format !== 'duration_ms' || !descriptor.percentile) {
                singleMetricCharts.push({ title: descriptor.label, descriptors: [descriptor], latency: false });
                return;
            }
            if (!latencyGroups.has(descriptor.key)) {
                latencyGroups.set(descriptor.key, {
                    title: descriptor.label.replace(/\s+(Avg|P90|P99)$/, ''),
                    descriptors: [],
                    latency: true,
                });
            }
            latencyGroups.get(descriptor.key).descriptors.push(descriptor);
        });
        const percentileOrder = { Avg: 0, P90: 1, P99: 2 };
        const groupedCharts = [
            ...latencyGroups.values().map((group) => ({
                ...group,
                descriptors: group.descriptors.sort((a, b) =>
                    percentileOrder[percentileLabel(a)] - percentileOrder[percentileLabel(b)]),
            })),
            ...singleMetricCharts,
        ];
        const latencyColors = { Avg: '#60a5fa', P90: '#68c66f', P99: '#f97316' };

        container.innerHTML = groupedCharts.map((chart, index) => {
            const primaryDescriptor = chart.descriptors[0];
            const accent = chart.latency ? latencyColors[percentileLabel(primaryDescriptor)] : colors[index % colors.length];
            const current = this._liveValue(primaryDescriptor, this._latestMetrics?.[primaryDescriptor.key]);
            const { average, peak } = this._liveWindowStats(primaryDescriptor);
            const footer = chart.latency
                ? `<div class="obs-live-chart-legend">${chart.descriptors.map((descriptor) => {
                    const label = percentileLabel(descriptor);
                    return `<span><i style="--legend-color:${latencyColors[label]}"></i>${label}</span>`;
                }).join('')}</div>`
                : `<div class="obs-live-chart-meta"><span>60s avg ${formatMetricValue(average, primaryDescriptor.format, primaryDescriptor.unit)}</span><span>peak ${formatMetricValue(peak, primaryDescriptor.format, primaryDescriptor.unit)}</span></div>`;
            return `<article class="obs-live-chart" style="--chart-accent:${accent}">
                <div class="obs-live-chart-head">
                    <div>
                        <span class="obs-live-chart-title">${this._escapeHtml(chart.title)}</span>
                        <span class="obs-live-chart-window">last 5 min</span>
                    </div>
                    ${chart.latency ? '' : `<strong>${formatMetricValue(current, primaryDescriptor.format, primaryDescriptor.unit)}</strong>`}
                </div>
                <div class="obs-live-chart-canvas" id="obs-live-chart-${index}"></div>
                ${footer}
            </article>`;
        }).join('');

        groupedCharts.forEach((chart, index) => {
            const host = document.getElementById(`obs-live-chart-${index}`);
            if (!host) return;
            const timestamps = this._liveHistory.map((point) => new Date(point.timestamp).getTime() / 1000);
            const values = chart.descriptors.map((descriptor) =>
                this._liveHistory.map((point) => point[descriptor.historyKey] ?? null));
            const entry = this._latestMetrics?.[chart.descriptors[0].key];
            try {
                this._liveCharts.push(createLineChart({
                    width: Math.max(120, host.parentElement.clientWidth - 18),
                    height: 220,
                    series: [{ label: 'Time' }, ...chart.descriptors.map((descriptor, seriesIndex) => {
                        const label = percentileLabel(descriptor);
                        return {
                            label: chart.latency ? label : descriptor.label,
                            stroke: chart.latency ? latencyColors[label] : colors[(index + seriesIndex) % colors.length],
                            width: 1,
                        };
                    })],
                    axes: [{ stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } }, { stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } }],
                    scales: { x: { time: true } },
                    tooltip: {
                        title: chart.title,
                        modelName: this._currentModelName || this._modelNameFromLabels(entry?.labels),
                        formatter: (value, seriesIndex) => {
                            const descriptor = chart.descriptors[seriesIndex] || chart.descriptors[0];
                            return formatMetricValue(value, descriptor.format, descriptor.unit);
                        },
                    },
                }, [timestamps, ...values], host));
            } catch (error) {
                host.textContent = `Chart unavailable: ${error.message}`;
            }
        });
        return;

        container.innerHTML = chartMetrics.map((descriptor, index) => {
            const current = this._liveValue(descriptor, this._latestMetrics?.[descriptor.key]);
            const { average, peak } = this._liveWindowStats(descriptor);
            return `<article class="obs-live-chart" style="--chart-accent:${colors[index % colors.length]}">
                <div class="obs-live-chart-head">
                    <div>
                        <span class="obs-live-chart-title">${this._escapeHtml(descriptor.label)}</span>
                        <span class="obs-live-chart-window">最近 5 分钟</span>
                    </div>
                    <strong>${formatMetricValue(current, descriptor.format, descriptor.unit)}</strong>
                </div>
                <div class="obs-live-chart-canvas" id="obs-live-chart-${index}"></div>
                <div class="obs-live-chart-meta"><span>60 秒均值 ${formatMetricValue(average, descriptor.format, descriptor.unit)}</span><span>峰值 ${formatMetricValue(peak, descriptor.format, descriptor.unit)}</span></div>
            </article>`;
        }).join('');
        if (this._liveHistory.length === 0) return;

        chartMetrics.forEach((descriptor, index) => {
            const host = document.getElementById(`obs-live-chart-${index}`);
            if (!host) return;
            const timestamps = this._liveHistory.map((point) => new Date(point.timestamp).getTime() / 1000);
            const values = this._liveHistory.map((point) => point[descriptor.historyKey] ?? null);
            const entry = this._latestMetrics?.[descriptor.key];
            try {
                this._liveCharts.push(createLineChart({
                    width: Math.max(120, host.parentElement.clientWidth - 18),
                    height: 220,
                    series: [{ label: 'Time' }, { label: descriptor.label, stroke: colors[index], width: 1 }],
                    axes: [{ stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } }, { stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } }],
                    scales: { x: { time: true } },
                    tooltip: {
                        title: descriptor.label,
                        modelName: this._currentModelName || this._modelNameFromLabels(entry?.labels),
                        formatter: (value) => formatMetricValue(value, descriptor.format, descriptor.unit),
                    },
                }, [timestamps, values], host));
            } catch (error) {
                host.textContent = `Chart unavailable: ${error.message}`;
            }
        });
    },

    // -- Overview tab -------------------------------------------------------

    _renderOverview(metrics) {
        const container = document.getElementById('obs-overview-cards');
        if (!container) return;
        const noData = document.getElementById('obs-overview-no-data');
        if (noData) noData.style.display = 'none';
        const existingGroups = container.querySelectorAll('.obs-category-group');
        existingGroups.forEach((g) => g.remove());
    },

    // -- Alerts -------------------------------------------------------------

    _getThresholds(key) {
        if (this._customThresholds && this._customThresholds[key]) {
            return this._customThresholds[key];
        }
        const reg = METRIC_REGISTRY[key];
        return reg ? reg.thresholds : null;
    },

    _loadAlertThresholds() {
        try {
            const stored = localStorage.getItem('obs-alert-thresholds');
            if (stored) this._customThresholds = JSON.parse(stored);
        } catch { /* ignore */ }
    },

    _saveAlertThresholds() {
        try {
            localStorage.setItem('obs-alert-thresholds', JSON.stringify(this._customThresholds));
        } catch { /* ignore */ }
    },

    _renderAlerts(metrics) {
        const container = document.getElementById('obs-alerts');
        if (!container) return;

        let html = '';
        for (const [key, entry] of Object.entries(metrics)) {
            const reg = METRIC_REGISTRY[key];
            if (!reg) continue;
            const thresholds = this._getThresholds(key);
            if (!thresholds) continue;
            const value = entry.value ?? null;
            if (value == null) continue;
            const status = getThresholdStatus(value, thresholds);
            if (status === 'ok') {
                this._alertedMetrics.delete(key);
                continue;
            }

            const label = reg.label || key;
            const formatted = formatMetricValue(value, reg.format, reg.unit);
            const level = status === 'danger' ? 'danger' : 'warning';
            const threshVal = status === 'danger' ? thresholds.danger : thresholds.warning;
            const threshDisplay = reg.format === 'percent' ? `${(threshVal * 100).toFixed(0)}%` : threshVal;
            html += `<div class="obs-alert ${level}">
                <strong>${this._escapeHtml(label)}</strong>: ${formatted}
                (threshold: ${threshDisplay})
            </div>`;

            if (!this._alertedMetrics.has(key)) {
                this._alertedMetrics.add(key);
                if (this.ui && this.ui.showNotification) {
                    this.ui.showNotification(
                        `${label}: ${formatted} (${level})`,
                        level === 'danger' ? 'error' : 'warning',
                        5000
                    );
                }
                this._alertHistory.unshift({
                    time: new Date(),
                    label,
                    formatted,
                    level,
                });
                if (this._alertHistory.length > 20) this._alertHistory.pop();
            }
        }
        container.innerHTML = html;
        this._renderAlertHistory();
    },

    _renderAlertHistory() {
        const container = document.getElementById('obs-alert-history');
        const list = document.getElementById('obs-alert-history-list');
        if (!container || !list) return;

        if (this._alertHistory.length === 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';
        let html = '';
        for (const a of this._alertHistory) {
            const t = a.time;
            const ts = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}`;
            html += `<div class="obs-alert-history-item ${a.level}">
                <span class="alert-time">${ts}</span>
                <strong>${this._escapeHtml(a.label)}</strong>: ${a.formatted}
            </div>`;
        }
        list.innerHTML = html;
    },

    _showAlertSettings() {
        let overlay = document.getElementById('obs-alert-settings-overlay');
        if (overlay) {
            overlay.classList.toggle('visible');
            return;
        }

        overlay = document.createElement('div');
        overlay.id = 'obs-alert-settings-overlay';
        overlay.className = 'obs-alert-settings visible';
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.classList.remove('visible');
        });

        const thresholdMetrics = Object.entries(METRIC_REGISTRY).filter(([, r]) => r.thresholds);
        let rows = '';
        for (const [key, reg] of thresholdMetrics) {
            const t = this._getThresholds(key) || reg.thresholds;
            const isPct = reg.format === 'percent';
            const warnDisplay = isPct ? (t.warning * 100) : t.warning;
            const dangerDisplay = isPct ? (t.danger * 100) : t.danger;
            const suffix = isPct ? '%' : '';
            rows += `<tr>
                <td>${this._escapeHtml(reg.label)}${suffix ? ` (${suffix})` : ''}</td>
                <td><input type="number" data-key="${key}" data-level="warning" data-pct="${isPct}" value="${warnDisplay}" /></td>
                <td><input type="number" data-key="${key}" data-level="danger" data-pct="${isPct}" value="${dangerDisplay}" /></td>
            </tr>`;
        }

        overlay.innerHTML = `<div class="obs-alert-settings-panel">
            <h3>Alert Thresholds</h3>
            <table>
                <thead><tr><th>Metric</th><th>Warning</th><th>Danger</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:16px; display:flex; gap:8px; justify-content:flex-end;">
                <button class="obs-btn" id="obs-alert-reset-btn">Reset Defaults</button>
                <button class="obs-btn obs-btn-primary" id="obs-alert-save-btn">Save</button>
            </div>
        </div>`;

        document.body.appendChild(overlay);

        document.getElementById('obs-alert-save-btn').addEventListener('click', () => {
            if (!this._customThresholds) this._customThresholds = {};
            overlay.querySelectorAll('input[type="number"]').forEach((inp) => {
                const key = inp.dataset.key;
                const level = inp.dataset.level;
                const isPct = inp.dataset.pct === 'true';
                if (!this._customThresholds[key]) {
                    const reg = METRIC_REGISTRY[key];
                    this._customThresholds[key] = { ...reg.thresholds };
                }
                let val = parseFloat(inp.value);
                if (isPct) val = val / 100;
                this._customThresholds[key][level] = val;
            });
            this._saveAlertThresholds();
            overlay.classList.remove('visible');
        });

        document.getElementById('obs-alert-reset-btn').addEventListener('click', () => {
            this._customThresholds = null;
            localStorage.removeItem('obs-alert-thresholds');
            overlay.querySelectorAll('input[type="number"]').forEach((inp) => {
                const reg = METRIC_REGISTRY[inp.dataset.key];
                if (reg && reg.thresholds) {
                    const isPct = inp.dataset.pct === 'true';
                    const raw = reg.thresholds[inp.dataset.level];
                    inp.value = isPct ? raw * 100 : raw;
                }
            });
        });
    },

    // -- All Metrics table --------------------------------------------------

    _renderAllMetricsTable() {
        const tbody = document.getElementById('obs-metrics-tbody');
        const countEl = document.getElementById('obs-metric-count');
        if (!tbody || !this._latestMetrics) return;

        let rows = [];
        for (const [key, entry] of Object.entries(this._latestMetrics)) {
            if (entry.type === 'histogram_bucket') continue;

            const reg = METRIC_REGISTRY[key] || null;
            const value = entry.value ?? entry.p50 ?? null;
            const type = entry.type || 'unknown';
            const labels = entry.labels || '';
            const catId = reg ? reg.category : 'other';
            const cat = CATEGORIES[catId] || CATEGORIES['other'];

            if (this._searchFilter) {
                const searchTarget = `${key} ${type} ${catId} ${cat.title} ${labels}`.toLowerCase();
                if (!searchTarget.includes(this._searchFilter)) continue;
            }

            rows.push({ key, value, type, labels, catId, catTitle: cat.title, reg });
        }

        rows.sort((a, b) => {
            let cmp = 0;
            switch (this._sortColumn) {
                case 'name':     cmp = a.key.localeCompare(b.key); break;
                case 'type':     cmp = a.type.localeCompare(b.type); break;
                case 'value':    cmp = (a.value ?? -Infinity) - (b.value ?? -Infinity); break;
                case 'category': cmp = a.catTitle.localeCompare(b.catTitle); break;
            }
            return this._sortAsc ? cmp : -cmp;
        });

        let html = '';
        for (const row of rows) {
            const fmt = row.reg ? row.reg.format : this._guessFormat(row.key, this._latestMetrics[row.key]);
            const formatted = formatMetricValue(row.value, fmt, row.reg?.unit);
            html += `<tr>
                <td class="metric-name">${this._escapeHtml(row.key)}</td>
                <td><span class="metric-badge ${row.type}">${row.type}</span></td>
                <td>${formatted}</td>
                <td>${this._escapeHtml(row.catTitle)}</td>
                <td class="metric-labels">${this._escapeHtml(row.labels)}</td>
            </tr>`;
        }

        tbody.innerHTML = html;
        if (countEl) countEl.textContent = `${rows.length} metrics`;
    },

    _updateSortArrows() {
        document.querySelectorAll('#obs-metrics-table th[data-sort]').forEach((th) => {
            const arrow = th.querySelector('.sort-arrow');
            if (!arrow) return;
            if (th.dataset.sort === this._sortColumn) {
                arrow.textContent = this._sortAsc ? '▲' : '▼';
                arrow.classList.add('active');
            } else {
                arrow.textContent = '';
                arrow.classList.remove('active');
            }
        });
    },

    // -- Time Series tab ----------------------------------------------------

    _initTsPicker() {
        const picker = document.getElementById('obs-ts-picker');
        if (!picker) return;

        const backend = this._latestBackend === 'sglang' ? 'sglang' : 'vllm';
        const preferredMetrics = backend === 'sglang'
            ? ['sglang:token_usage', 'sglang:num_queue_reqs', 'sglang:gen_throughput']
            : ['vllm:kv_cache_usage_perc', 'vllm:num_requests_running', 'vllm:avg_generation_throughput_toks_per_s'];
        const derivedMetrics = backend === 'sglang'
            ? [
                'observability:prompt_token_rate',
                'observability:generation_token_rate',
                'observability:total_token_rate',
            ]
            : Object.keys(METRIC_REGISTRY).filter((key) => key.startsWith('observability:'));

        const registeredKeys = Object.keys(METRIC_REGISTRY).filter((k) => {
            const r = METRIC_REGISTRY[k];
            return (k.startsWith(`${backend}:`) || derivedMetrics.includes(k)) && r.format !== 'duration_ms';
        });
        const discoveredKeys = Object.entries(this._latestMetrics || {})
            .filter(([key, entry]) => {
                const isCurrentBackend = key.startsWith(`${backend}:`) || derivedMetrics.includes(key);
                const isNumeric = Number.isFinite(entry?.value) || Number.isFinite(entry?.p50);
                const isLowLevelSeries = entry?.type === 'histogram_bucket'
                    || /_(?:bucket(?:_le_.*)?|sum|count|created)$/.test(key);
                return isCurrentBackend && isNumeric && !isLowLevelSeries && METRIC_REGISTRY[key]?.format !== 'duration_ms';
            })
            .map(([key]) => key);
        const allKeys = [...new Set([...registeredKeys, ...discoveredKeys])];
        const availableKeys = allKeys.filter((key) => Object.hasOwn(this._latestMetrics || {}, key));
        const pickerKeys = availableKeys.length ? availableKeys : allKeys;
        const pickerSignature = pickerKeys.join('|');
        if (picker.dataset.backend === backend && picker.dataset.metricSignature === pickerSignature && picker.children.length > 0) return;

        const defaultMetrics = preferredMetrics.filter((key) => pickerKeys.includes(key));
        for (const key of pickerKeys) {
            if (defaultMetrics.length >= 3) break;
            if (!defaultMetrics.includes(key)) defaultMetrics.push(key);
        }
        const retainedMetrics = picker.dataset.backend === backend
            ? [...this._tsSelectedMetrics].filter((key) => pickerKeys.includes(key))
            : [];
        this._tsSelectedMetrics = new Set(retainedMetrics.length ? retainedMetrics : defaultMetrics);

        let html = '';
        for (const key of pickerKeys) {
            const reg = METRIC_REGISTRY[key] || {
                label: key.replace(/^(vllm:|sglang:|observability:)/, '').replace(/_/g, ' '),
            };
            const checked = this._tsSelectedMetrics.has(key) ? 'checked' : '';
            html += `<label><input type="checkbox" value="${key}" ${checked} /> ${this._escapeHtml(reg.label)}</label>`;
        }
        picker.innerHTML = html;
        picker.dataset.backend = backend;
        picker.dataset.metricSignature = pickerSignature;
        this._updateTsPickerSummary();

        picker.addEventListener('change', (e) => {
            if (e.target.type !== 'checkbox') return;
            if (e.target.checked) {
                this._tsSelectedMetrics.add(e.target.value);
            } else {
                this._tsSelectedMetrics.delete(e.target.value);
            }
            this._updateTsPickerSummary();
            this._buildChart();
        });
    },

    _updateTsPickerSummary() {
        const count = document.getElementById('obs-ts-picker-count');
        if (count) count.textContent = `${this._tsSelectedMetrics.size} 项已选`;
    },

    async _loadTimeSeries() {
        const noData = document.getElementById('obs-ts-no-data');
        const hint = document.getElementById('obs-ts-history-hint');
        const hintText = document.getElementById('obs-ts-hint-text');
        const hintBtn = document.getElementById('obs-ts-hint-btn');
        const defaultMsg = document.getElementById('obs-ts-no-data-msg');

        try {
            this._tsHistory = await metricsPoller.getHistory(null, this._tsSeconds);
        } catch {
            this._tsHistory = [];
        }

        if (this._tsHistory.length === 0) {
            if (noData) noData.style.display = '';
            const wrap = document.getElementById('obs-ts-chart-wrap');
            if (wrap) wrap.style.display = 'none';
            const liveBar = document.getElementById('obs-ts-live-bar');
            if (liveBar) liveBar.style.display = 'none';

            if (hint) hint.style.display = 'none';
            if (defaultMsg) defaultMsg.style.display = '';

            try {
                const summary = await metricsPoller.getHistorySummary();
                if (summary && summary.total > 0 && summary.oldest_age_seconds > 0) {
                    const age = summary.oldest_age_seconds;
                    const ageLabel = age >= 3600
                        ? `${(age / 3600).toFixed(1)} hours`
                        : age >= 60
                            ? `${Math.round(age / 60)} min`
                            : `${Math.round(age)} sec`;
                    const spanLabel = summary.span_seconds >= 60
                        ? `${Math.round(summary.span_seconds / 60)} min`
                        : `${Math.round(summary.span_seconds)} sec`;
                    if (hintText) {
                        hintText.textContent =
                            `${summary.total} data points from ${ageLabel} ago (spanning ${spanLabel}) — outside the current window.`;
                    }
                    if (defaultMsg) defaultMsg.style.display = 'none';
                    if (hint) hint.style.display = '';
                    if (hintBtn) {
                        hintBtn.onclick = () => {
                            const needed = Math.ceil(summary.oldest_age_seconds) + 60;
                            this._tsSeconds = needed;
                            document.querySelectorAll('.obs-ts-range').forEach((b) => b.classList.remove('active'));
                            const customVal = document.getElementById('obs-ts-custom-val');
                            const customUnit = document.getElementById('obs-ts-custom-unit');
                            if (needed >= 60 && customVal && customUnit) {
                                customVal.value = Math.ceil(needed / 60);
                                customUnit.value = 'm';
                            } else if (customVal && customUnit) {
                                customVal.value = needed;
                                customUnit.value = 's';
                            }
                            this._loadTimeSeries();
                        };
                    }
                }
            } catch { /* summary fetch is best-effort */ }
            return;
        }

        if (noData) noData.style.display = 'none';
        const wrap = document.getElementById('obs-ts-chart-wrap');
        if (wrap) wrap.style.display = '';

        const liveBar = document.getElementById('obs-ts-live-bar');
        if (liveBar) {
            const hasActivePreset = document.querySelector('.obs-ts-range.active') !== null;
            liveBar.style.display = hasActivePreset ? 'none' : 'flex';
        }

        this._buildChart();
    },

    _buildChart() {
        const wrap = document.getElementById('obs-ts-chart-wrap');
        if (!wrap) return;

        if (this._uplotChart) {
            this._uplotChart.destroy();
            this._uplotChart = null;
        }

        const selected = [...this._tsSelectedMetrics];
        if (selected.length === 0 || this._tsHistory.length === 0) return;

        const timestamps = this._tsHistory.map((s) => {
            const d = new Date(s.timestamp);
            return d.getTime() / 1000;
        });

        const series = [{ label: 'Time' }];
        const data = [timestamps];

        const colors = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#ec4899', '#14b8a6', '#f97316'];

        for (let i = 0; i < selected.length; i++) {
            const key = selected[i];
            const reg = METRIC_REGISTRY[key] || {};
            series.push({
                label: reg.label || key.replace('vllm:', ''),
                stroke: colors[i % colors.length],
                width: 1,
            });
            data.push(this._tsHistory.map((s) => {
                const v = s[key];
                return v != null ? v : null;
            }));
        }

        const width = wrap.clientWidth - 16;
        const height = Math.max(400, wrap.clientHeight - 16);

        const opts = {
            width,
            height,
            series,
            axes: [
                { stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } },
                { stroke: '#888', grid: { stroke: 'rgba(255,255,255,0.06)' } },
            ],
            cursor: { sync: { key: 'obs' } },
            scales: { x: { time: true } },
            tooltip: {
                title: '时序指标',
                modelName: this._currentModelName || this._modelNameFromLabels(this._latestMetrics?.[selected[0]]?.labels),
            },
        };

        wrap.innerHTML = '';
        try {
            this._uplotChart = createLineChart(opts, data, wrap);
        } catch (e) {
            console.error('uPlot init error:', e);
            wrap.innerHTML = `<div style="padding:20px;color:var(--text-secondary)">Chart error: ${e.message}</div>`;
        }
    },

    _appendLivePoint(metrics) {
        if (!this._uplotChart || this._tsSelectedMetrics.size === 0) return;
        const now = Date.now() / 1000;
        const selected = [...this._tsSelectedMetrics];

        const newData = this._uplotChart.data.map((arr) => [...arr]);
        newData[0].push(now);

        for (let i = 0; i < selected.length; i++) {
            const key = selected[i];
            const entry = metrics[key];
            const val = entry ? (entry.value ?? entry.p50 ?? null) : null;
            newData[i + 1].push(val);
        }

        const cutoff = now - this._tsSeconds;
        let start = 0;
        while (start < newData[0].length && newData[0][start] < cutoff) start++;
        if (start > 0) {
            for (let i = 0; i < newData.length; i++) {
                newData[i] = newData[i].slice(start);
            }
        }

        this._uplotChart.setData(newData);
    },

    // -- Latency tab --------------------------------------------------------

    _renderLatency(metrics) {
        const summaryEl = document.getElementById('obs-latency-summary');
        const histEl = document.getElementById('obs-latency-histograms');
        const noData = document.getElementById('obs-latency-no-data');
        if (!summaryEl || !histEl) return;

        const latencyMetrics = Object.entries(METRIC_REGISTRY)
            .filter(([, r]) => r.histogramDisplay)
            .map(([key, reg]) => ({ key, reg, entry: metrics[key] }))
            .filter(({ entry }) => entry);

        if (latencyMetrics.length === 0) {
            if (noData) noData.style.display = '';
            summaryEl.innerHTML = '';
            histEl.innerHTML = '';
            return;
        }
        if (noData) noData.style.display = 'none';

        const percentiles = ['avg', 'p90', 'p99'];

        const globalMaxSec = Math.max(
            ...latencyMetrics.map(({ entry }) =>
                Math.max(...percentiles.map(p => entry[p] ?? 0))
            ), 0.001
        );

        let tableHtml = `<table class="obs-latency-table">
            <thead><tr>
                <th style="width:22%">Metric</th>
                ${percentiles.map(p => `<th style="width:13%">${p.toUpperCase()}</th>`).join('')}
                <th style="width:39%">Distribution</th>
            </tr></thead><tbody>`;

        for (const { key, reg, entry } of latencyMetrics) {
            tableHtml += `<tr><td>${this._escapeHtml(reg.label)}</td>`;
            for (const p of percentiles) {
                const val = entry[p];
                if (val == null) {
                    tableHtml += `<td>--</td>`;
                } else {
                    const ms = val * 1000;
                    const display = this._formatMs(ms);
                    const cls = ms > 2000 ? 'obs-latency-val-bad'
                              : ms > 500  ? 'obs-latency-val-warn'
                              : 'obs-latency-val-good';
                    tableHtml += `<td class="${cls}">${display}</td>`;
                }
            }

            if (!Number.isFinite(entry.p90) || !Number.isFinite(entry.p99)) {
                tableHtml += '<td>--</td>';
            } else {
                const scale = globalMaxSec * 1.05;
                const p90Pct = (entry.p90 / scale) * 100;
                const p99Pct = (entry.p99 / scale) * 100;
                const fillPct = Math.min(p99Pct + 2, 100);
                tableHtml += `<td class="obs-pct-bar-cell">
                    <div class="obs-pct-bar">
                        <div class="obs-pct-bar-fill" style="width:${fillPct.toFixed(1)}%"></div>
                        <div class="obs-pct-pin obs-pct-pin-p90" style="left:${p90Pct.toFixed(1)}%"
                             title="p90: ${this._formatMs(entry.p90 * 1000)}"></div>
                        <div class="obs-pct-pin obs-pct-pin-p99" style="left:${p99Pct.toFixed(1)}%"
                             title="p99: ${this._formatMs(entry.p99 * 1000)}"></div>
                    </div>
                </td>`;
            }
            tableHtml += `</tr>`;
        }
        tableHtml += `</tbody></table>`;
        summaryEl.innerHTML = tableHtml;

        let histHtml = '';
        for (const { key, reg } of latencyMetrics) {
            const bucketKey = key + '_bucket';
            const rawBuckets = Object.entries(metrics)
                .filter(([k]) => k.startsWith(bucketKey))
                .filter(([, e]) => Number.isFinite(e.interval_value))
                .map(([, e]) => ({
                    le: e.labels ? this._extractLeRaw(e.labels) : Infinity,
                    leLabel: e.labels ? this._extractLe(e.labels) : 'Inf',
                    count: e.interval_value,
                }))
                .sort((a, b) => a.le - b.le);

            if (rawBuckets.length === 0) continue;

            const diffBuckets = [];
            let prevCount = 0;
            let prevLabel = '0';
            for (const b of rawBuckets) {
                const diff = Math.max(b.count - prevCount, 0);
                const rangeLabel = b.le === Infinity
                    ? `> ${prevLabel}`
                    : `${prevLabel} \u2013 ${b.leLabel}`;
                diffBuckets.push({ range: rangeLabel, count: diff, le: b.le });
                prevCount = b.count;
                prevLabel = b.leLabel;
            }

            const total = prevCount;
            if (total <= 0) continue;
            const maxDiff = Math.max(...diffBuckets.map(d => d.count), 1);
            const peakCount = maxDiff;

            histHtml += `<div class="obs-histogram-group">
                <div class="obs-diff-hist-title">${this._escapeHtml(reg.label)} Distribution</div>`;

            for (const d of diffBuckets) {
                if (d.count === 0 && d.le === Infinity) continue;
                const barPct = (d.count / maxDiff) * 100;
                const freqPct = ((d.count / total) * 100).toFixed(0);
                const isPeak = d.count === peakCount && d.count > 0;
                histHtml += `<div class="obs-diff-bar-row">
                    <span class="obs-diff-range">${d.range}</span>
                    <div class="obs-diff-bar-bg">
                        <div class="obs-diff-bar-fill${isPeak ? ' peak' : ''}" style="width:${barPct.toFixed(1)}%"></div>
                    </div>
                    <span class="obs-diff-count">${d.count}</span>
                    <span class="obs-diff-pct">${freqPct}%</span>
                </div>`;
            }
            histHtml += `</div>`;
        }
        histEl.innerHTML = histHtml;
    },

    _formatMs(ms) {
        if (ms < 1) return `${(ms * 1000).toFixed(0)} \u00b5s`;
        if (ms < 1000) return `${ms.toFixed(1)} ms`;
        return `${(ms / 1000).toFixed(2)} s`;
    },

    _extractLeRaw(labels) {
        const match = labels.match(/le="([^"]+)"/);
        if (!match) return Infinity;
        if (match[1] === '+Inf') return Infinity;
        return parseFloat(match[1]);
    },

    _extractLe(labels) {
        const match = labels.match(/le="([^"]+)"/);
        if (!match) return '?';
        const val = match[1];
        if (val === '+Inf') return 'Inf';
        const num = parseFloat(val);
        if (num < 0.001) return `${(num * 1e6).toFixed(0)}us`;
        if (num < 1) return `${(num * 1000).toFixed(0)}ms`;
        return `${num.toFixed(1)}s`;
    },

    // -- Demo / Clear -------------------------------------------------------

    _updateDemoButtons(source) {
        const demoBtn = document.getElementById('obs-demo-btn');
        const clearBtn = document.getElementById('obs-clear-btn');
        if (demoBtn) demoBtn.disabled = source !== 'none';
        if (clearBtn) clearBtn.disabled = source !== 'simulated';
    },

    async _runDemo() {
        try {
            await fetch('/api/vllm/metrics/simulate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    kv_cache_usage_perc: 45.2,
                    prefix_cache_hit_rate: 62.5,
                    num_preemptions: 2,
                    num_requests_running: 3,
                    num_requests_waiting: 1,
                    prefix_cache_hits: 1250,
                    prefix_cache_queries: 2000,
                    gpu_cache_usage_perc: 38.7,
                    spec_decode_accepted: 180,
                    spec_decode_draft: 320,
                }),
            });
            const badge = document.getElementById('obs-simulated-badge');
            if (badge) badge.classList.add('visible');
        } catch (err) {
            console.error('Demo simulation failed:', err);
        }
    },

    async _clearDemo() {
        try {
            await fetch('/api/vllm/metrics/simulate/reset', { method: 'POST' });
            const badge = document.getElementById('obs-simulated-badge');
            if (badge) badge.classList.remove('visible');
            this._latestMetrics = null;
            this._alertHistory = [];
            this._alertedMetrics.clear();
            this._lastScrapeLocalRef = null;
            this._prevScrapeAge = null;
            this._showNoData(true, null);
            const alerts = document.getElementById('obs-alerts');
            if (alerts) alerts.innerHTML = '';
            this._renderAlertHistory();
            const tbody = document.getElementById('obs-metrics-tbody');
            if (tbody) tbody.innerHTML = '';
            const cards = document.getElementById('obs-overview-cards');
            if (cards) {
                cards.querySelectorAll('.obs-category-group').forEach((g) => g.remove());
            }
            if (this._uplotChart) {
                this._uplotChart.destroy();
                this._uplotChart = null;
            }
        } catch (err) {
            console.error('Clear failed:', err);
        }
    },

    // -- Export --------------------------------------------------------------

    _exportJSON() {
        if (!this._latestMetrics) return;
        const blob = new Blob(
            [JSON.stringify(this._latestMetrics, null, 2)],
            { type: 'application/json' }
        );
        this._download(blob, `vllm-metrics-${this._timestamp()}.json`);
    },

    _exportCSV() {
        if (!this._latestMetrics) return;
        const header = 'name,type,value,labels,category\n';
        let csv = header;
        for (const [key, entry] of Object.entries(this._latestMetrics)) {
            const reg = METRIC_REGISTRY[key];
            const cat = reg ? (CATEGORIES[reg.category]?.title || reg.category) : 'Other';
            const value = entry.value ?? entry.p50 ?? '';
            const type = entry.type || 'unknown';
            const labels = (entry.labels || '').replace(/"/g, '""');
            csv += `"${key}","${type}",${value},"${labels}","${cat}"\n`;
        }
        const blob = new Blob([csv], { type: 'text/csv' });
        this._download(blob, `vllm-metrics-${this._timestamp()}.csv`);
    },

    _exportTimeSeries() {
        if (!this._tsHistory || this._tsHistory.length === 0) return;
        const blob = new Blob(
            [JSON.stringify(this._tsHistory, null, 2)],
            { type: 'application/json' }
        );
        this._download(blob, `vllm-timeseries-${this._timestamp()}.json`);
    },

    _exportLatency() {
        if (!this._latestMetrics) return;
        const latencyData = {};
        for (const [key, reg] of Object.entries(METRIC_REGISTRY)) {
            if (!reg.histogramDisplay) continue;
            const entry = this._latestMetrics[key];
            if (!entry) continue;
            latencyData[key] = {
                label: reg.label,
                avg: entry.avg,
                p90: entry.p90,
                p99: entry.p99,
            };
        }
        const blob = new Blob(
            [JSON.stringify(latencyData, null, 2)],
            { type: 'application/json' }
        );
        this._download(blob, `vllm-latency-${this._timestamp()}.json`);
    },

    _download(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    },

    _timestamp() {
        return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    },

    // -- Utilities ----------------------------------------------------------

    _guessFormat(key, entry) {
        if (!entry) return 'number';
        const type = entry.type || '';
        if (type === 'histogram') {
            const k = (key || '').toLowerCase();
            if (/seconds|latency|time/.test(k)) return 'duration_ms';
            return 'number';
        }
        return 'number';
    },

    _escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    },
};

export default ObservabilityModule;
