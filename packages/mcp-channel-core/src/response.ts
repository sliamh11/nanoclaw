export enum McpErrorCode {
  USAGE = 2,
  NOT_FOUND = 3,
  AUTH = 4,
  API_ERROR = 5,
  RATE_LIMIT = 7,
}

export interface McpResponseOptions {
  compact?: boolean;
  select?: string;
  truncateAt?: number;
}

type McpTextContent = { type: 'text'; text: string };
type McpToolResult = { content: McpTextContent[] };
type McpErrorResult = McpToolResult & { isError: true };

function stripNulls(obj: unknown, truncateAt: number): unknown {
  if (Array.isArray(obj)) {
    return obj.map((item) => stripNulls(item, truncateAt));
  }
  if (obj !== null && typeof obj === 'object') {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (value === null || value === undefined) continue;
      if (typeof value === 'string' && value.length > truncateAt) {
        result[key] = value.slice(0, truncateAt) + '...';
      } else {
        result[key] = stripNulls(value, truncateAt);
      }
    }
    return result;
  }
  return obj;
}

function selectFields(obj: unknown, paths: string | undefined): unknown {
  if (!paths) return obj;

  const fields = paths
    .split(',')
    .map((f) => f.trim())
    .filter(Boolean);
  if (fields.length === 0) return obj;

  if (Array.isArray(obj)) {
    return obj.map((item) => selectFields(item, paths));
  }

  if (obj === null || typeof obj !== 'object') return obj;

  const record = obj as Record<string, unknown>;
  const grouped = new Map<string, string[]>();
  const directKeys: string[] = [];
  for (const path of fields) {
    const [key, ...rest] = path.split('.');
    if (!(key in record)) continue;
    if (rest.length === 0) {
      directKeys.push(key);
    } else {
      const existing = grouped.get(key) ?? [];
      existing.push(rest.join('.'));
      grouped.set(key, existing);
    }
  }
  const result: Record<string, unknown> = {};
  for (const key of directKeys) {
    result[key] = record[key];
  }
  for (const [key, subPaths] of grouped) {
    const nested = record[key];
    const subSelect = subPaths.join(',');
    if (Array.isArray(nested)) {
      result[key] = nested.map((item) => selectFields(item, subSelect));
    } else if (nested !== null && typeof nested === 'object') {
      result[key] = selectFields(nested, subSelect);
    } else {
      result[key] = nested;
    }
  }
  return result;
}

export function mcpResponse(
  data: unknown,
  opts?: McpResponseOptions,
): McpToolResult {
  let processed = data;
  if (opts?.select) {
    processed = selectFields(processed, opts.select);
  }
  if (opts?.compact) {
    processed = stripNulls(processed, opts?.truncateAt ?? 300);
  }
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(processed) }],
  };
}

export function mcpError(
  code: McpErrorCode,
  message: string,
  resource?: string,
): McpErrorResult {
  const payload: Record<string, unknown> = { error_code: code, message };
  if (resource) payload.resource = resource;
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(payload) }],
    isError: true,
  };
}
