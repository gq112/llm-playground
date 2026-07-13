/**
 * Metric Registry -- display metadata for vLLM and SGLang Prometheus metrics.
 *
 * Adding a new curated metric = one entry here.  Unregistered metrics
 * auto-appear in the All Metrics table with sensible defaults.
 */

export const METRIC_REGISTRY = {
    // --- KV Cache & Memory ---
    'vllm:kv_cache_usage_perc': {
        category: 'kv-cache',
        label: 'KV Cache Usage',
        format: 'percent',
        thresholds: { warning: 0.70, danger: 0.90 },
        sidebar: true,
        obsTab: 'overview',
    },
    'vllm:gpu_cache_usage_perc': {
        category: 'kv-cache',
        label: 'GPU Cache Usage',
        format: 'percent',
        thresholds: { warning: 0.70, danger: 0.90 },
        obsTab: 'overview',
    },
    'vllm:cpu_cache_usage_perc': {
        category: 'kv-cache',
        label: 'CPU Cache Usage',
        format: 'percent',
        thresholds: { warning: 0.80, danger: 0.95 },
        obsTab: 'overview',
    },
    'vllm:prefix_cache_hit_rate': {
        category: 'kv-cache',
        label: 'Prefix Cache Hit Rate',
        format: 'percent',
        obsTab: 'overview',
    },
    'vllm:prefix_cache_hits': {
        category: 'kv-cache',
        label: 'Prefix Cache Hits',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:prefix_cache_queries': {
        category: 'kv-cache',
        label: 'Prefix Cache Queries',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:num_preemptions': {
        category: 'kv-cache',
        label: 'Preemptions',
        format: 'integer',
        thresholds: { warning: 1, danger: 5 },
        obsTab: 'overview',
    },

    // --- Request Queue ---
    'vllm:num_requests_running': {
        category: 'requests',
        label: 'Running Requests',
        format: 'integer',
        sidebar: true,
        obsTab: 'overview',
    },
    'vllm:num_requests_waiting': {
        category: 'requests',
        label: 'Waiting Requests',
        format: 'integer',
        thresholds: { warning: 10, danger: 50 },
        sidebar: true,
        obsTab: 'overview',
    },

    // --- Throughput ---
    'vllm:avg_prompt_throughput_toks_per_s': {
        category: 'throughput',
        label: 'Avg Prompt Throughput',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'vllm:avg_generation_throughput_toks_per_s': {
        category: 'throughput',
        label: 'Avg Generation Throughput',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },

    // --- Speculative Decoding ---
    'vllm:spec_decode_num_accepted_tokens': {
        category: 'spec-decode',
        label: 'Accepted Tokens',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:spec_decode_num_draft_tokens': {
        category: 'spec-decode',
        label: 'Draft Tokens',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:spec_decode_num_drafts': {
        category: 'spec-decode',
        label: 'Drafts',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:spec_decode_acceptance_rate': {
        category: 'spec-decode',
        label: 'Acceptance Rate',
        format: 'percent',
        sidebar: true,
        obsTab: 'overview',
    },

    // --- Latency ---
    'vllm:e2e_request_latency_seconds': {
        category: 'latency',
        label: 'E2E Request Latency',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'vllm:time_to_first_token_seconds': {
        category: 'latency',
        label: 'Time to First Token',
        format: 'duration_ms',
        thresholds: { warning: 500, danger: 2000 },
        sidebar: true,
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'vllm:inter_token_latency_seconds': {
        category: 'latency',
        label: 'Inter-Token Latency',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'vllm:request_time_per_output_token_seconds': {
        category: 'latency',
        label: 'Time per Output Token',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },

    // --- SGLang: KV cache & queue ---
    'sglang:token_usage': {
        category: 'kv-cache',
        label: 'KV Token Usage',
        format: 'percent',
        thresholds: { warning: 0.70, danger: 0.90 },
        obsTab: 'overview',
    },
    'sglang:cache_hit_rate': {
        category: 'kv-cache',
        label: 'Radix Cache Hit Rate',
        format: 'percent',
        obsTab: 'overview',
    },
    'sglang:num_used_tokens': {
        category: 'kv-cache',
        label: 'Used KV Tokens',
        format: 'integer',
        obsTab: 'overview',
    },
    'sglang:num_running_reqs': {
        category: 'requests',
        label: 'Running Requests',
        format: 'integer',
        obsTab: 'overview',
    },
    'sglang:num_queue_reqs': {
        category: 'requests',
        label: 'Queued Requests',
        format: 'integer',
        thresholds: { warning: 10, danger: 50 },
        obsTab: 'overview',
    },

    // --- SGLang: throughput & speculative decoding ---
    'sglang:gen_throughput': {
        category: 'throughput',
        label: 'Generation Throughput',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'sglang:prompt_tokens': {
        category: 'tokens',
        label: 'Input Tokens (Total)',
        format: 'integer',
        obsTab: 'overview',
    },
    'sglang:generation_tokens': {
        category: 'tokens',
        label: 'Output Tokens (Total)',
        format: 'integer',
        obsTab: 'overview',
    },
    'sglang:spec_num_steps': {
        category: 'spec-decode',
        label: 'Speculative Steps',
        format: 'integer',
        obsTab: 'overview',
    },
    'sglang:spec_num_draft_tokens': {
        category: 'spec-decode',
        label: 'Speculative Draft Tokens',
        format: 'integer',
        obsTab: 'overview',
    },

    // --- SGLang: latency histograms ---
    'sglang:time_to_first_token_seconds': {
        category: 'latency',
        label: 'Time to First Token',
        format: 'duration_ms',
        thresholds: { warning: 0.5, danger: 2 },
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'sglang:e2e_request_latency_seconds': {
        category: 'latency',
        label: 'E2E Request Latency',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'sglang:time_per_output_token_seconds': {
        category: 'latency',
        label: 'Time per Output Token',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },
    'sglang:per_stage_req_latency_seconds': {
        category: 'latency',
        label: 'Stage Request Latency',
        format: 'duration_ms',
        obsTab: 'latency',
        histogramDisplay: ['avg', 'p90', 'p99'],
    },

    // --- Derived token flow (counter delta / collection interval) ---
    'observability:prompt_token_rate': {
        category: 'tokens',
        label: 'Input Token Rate',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'vllm:prompt_tokens': {
        category: 'tokens',
        label: 'Input Tokens (Total)',
        format: 'integer',
        obsTab: 'overview',
    },
    'vllm:generation_tokens': {
        category: 'tokens',
        label: 'Output Tokens (Total)',
        format: 'integer',
        obsTab: 'overview',
    },
    'observability:generation_token_rate': {
        category: 'tokens',
        label: 'Output Token Rate',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'observability:total_token_rate': {
        category: 'tokens',
        label: 'Total Token Rate',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },

    // --- Derived vLLM speculative decoding efficiency ---
    'observability:spec_draft_token_rate': {
        category: 'spec-decode',
        label: 'Draft Token Rate',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'observability:spec_accepted_token_rate': {
        category: 'spec-decode',
        label: 'Accepted Token Rate',
        unit: 'tok/s',
        format: 'number',
        obsTab: 'overview',
    },
    'observability:spec_acceptance_rate': {
        category: 'spec-decode',
        label: 'Draft Acceptance Rate',
        format: 'percent',
        obsTab: 'overview',
    },
    'observability:spec_mean_accept_length': {
        category: 'spec-decode',
        label: 'Mean Accepted Length',
        unit: 'tok/draft',
        format: 'number',
        obsTab: 'overview',
    },
};

export const CATEGORIES = {
    'kv-cache':    { title: 'KV Cache & Memory',    icon: 'database' },
    'requests':    { title: 'Request Queue',         icon: 'list' },
    'throughput':  { title: 'Throughput',             icon: 'zap' },
    'tokens':      { title: 'Token Flow',             icon: 'activity' },
    'spec-decode': { title: 'Speculative Decoding',  icon: 'rocket' },
    'latency':     { title: 'Latency',               icon: 'clock' },
    'other':       { title: 'Other Metrics',         icon: 'bar-chart', autoPopulate: true },
};

/**
 * Format a metric value for display based on its format spec.
 * @param {number|null} value
 * @param {string} format - one of: percent, integer, number, duration_ms
 * @param {string} [unit]
 * @returns {string}
 */
export function formatMetricValue(value, format, unit) {
    if (value == null || isNaN(value)) return '--';
    switch (format) {
        case 'percent':
            return `${(value * 100).toFixed(1)}%`;
        case 'integer':
            return Math.round(value).toLocaleString();
        case 'number':
            return value.toFixed(1) + (unit ? ` ${unit}` : '');
        case 'duration_ms':
            return `${(value * 1000).toFixed(1)} ms`;
        default:
            return String(value);
    }
}

/**
 * Get the threshold status for a metric value.
 * @param {number} value
 * @param {object} thresholds - { warning: number, danger: number }
 * @returns {'ok'|'warning'|'danger'}
 */
export function getThresholdStatus(value, thresholds) {
    if (!thresholds || value == null) return 'ok';
    if (value >= thresholds.danger) return 'danger';
    if (value >= thresholds.warning) return 'warning';
    return 'ok';
}

/**
 * Derive a legacy flat dict (compatible with /api/vllm/metrics) from the
 * structured metrics returned by /api/vllm/metrics/all.
 */
const _LEGACY_KEY_MAP = {
    'vllm:kv_cache_usage_perc': 'kv_cache_usage_perc',
    'vllm:gpu_cache_usage_perc': 'gpu_cache_usage_perc',
    'vllm:cpu_cache_usage_perc': 'cpu_cache_usage_perc',
    'vllm:prefix_cache_hit_rate': 'prefix_cache_hit_rate',
    'vllm:prefix_cache_hits': 'prefix_cache_hits',
    'vllm:prefix_cache_queries': 'prefix_cache_queries',
    'vllm:num_preemptions': 'num_preemptions',
    'vllm:num_requests_running': 'num_requests_running',
    'vllm:num_requests_waiting': 'num_requests_waiting',
    'vllm:avg_prompt_throughput_toks_per_s': 'avg_prompt_throughput',
    'vllm:avg_generation_throughput_toks_per_s': 'avg_generation_throughput',
    'vllm:spec_decode_num_accepted_tokens': 'spec_decode_accepted',
    'vllm:spec_decode_num_draft_tokens': 'spec_decode_draft',
    'vllm:spec_decode_num_drafts': 'spec_decode_num_drafts',
    'vllm:spec_decode_acceptance_rate': 'spec_decode_acceptance_rate',
};

const _PERCENT_KEYS = new Set([
    'vllm:kv_cache_usage_perc',
    'vllm:gpu_cache_usage_perc',
    'vllm:cpu_cache_usage_perc',
    'vllm:prefix_cache_hit_rate',
]);

export function toLegacyDict(metrics) {
    if (!metrics || typeof metrics !== 'object') return {};
    const out = {};
    for (const [canonical, legacy] of Object.entries(_LEGACY_KEY_MAP)) {
        const entry = metrics[canonical];
        if (!entry) continue;
        let val = entry.value ?? null;
        if (val != null && _PERCENT_KEYS.has(canonical)) {
            val = val * 100;
        }
        if (val != null) out[legacy] = val;
    }
    return out;
}

/**
 * Given the full metrics dict from /api/vllm/metrics/all, group metrics
 * by their registered category.  Unregistered metrics go into 'other'.
 * @param {object} metrics - { "vllm:foo": { value, type, labels }, ... }
 * @returns {object} - { categoryId: [ { key, entry, registry }, ... ], ... }
 */
export function groupByCategory(metrics) {
    const groups = {};
    for (const catId of Object.keys(CATEGORIES)) {
        groups[catId] = [];
    }

    for (const [key, entry] of Object.entries(metrics)) {
        if (entry.type === 'histogram_bucket') continue;
        const reg = METRIC_REGISTRY[key];
        const catId = reg ? reg.category : 'other';
        if (!groups[catId]) groups[catId] = [];
        groups[catId].push({ key, entry, registry: reg || null });
    }

    return groups;
}
