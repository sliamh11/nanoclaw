/**
 * Domain preset detection and loading.
 *
 * Reads preset markdown files from presets/ at project root, parses YAML
 * frontmatter for keywords, and detects which domains match a given prompt.
 * Returns concatenated preset content for injection into the agent prompt.
 *
 * Graceful: if presets/ doesn't exist or is empty, returns no domains.
 */
import fs from 'fs';
import path from 'path';

import { logger } from './logger.js';

/** Approximate token budget for combined preset content. ~4 chars per token. */
const MAX_PRESET_CHARS = 3200; // ~800 tokens

/** Minimum keyword hits to activate a domain. */
const MIN_KEYWORD_HITS = 2;

interface Preset {
  domain: string;
  keywords: string[];
  content: string; // Markdown body (after frontmatter)
}

let cachedPresets: Preset[] | null = null;

function presetsDir(): string {
  return path.join(process.cwd(), 'presets');
}

/**
 * Parse a preset markdown file with YAML frontmatter.
 * Expected format:
 * ---
 * domain: <name>
 * keywords: [kw1, kw2, ...]
 * ---
 * <markdown content>
 */
function parsePreset(filePath: string): Preset | null {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    const parts = raw.split('---');
    if (parts.length < 3) return null;

    const frontmatter = parts[1];
    const content = parts.slice(2).join('---').trim();

    // Parse domain
    const domainMatch = frontmatter.match(/domain:\s*(.+)/);
    if (!domainMatch) return null;
    const domain = domainMatch[1].trim();

    // Parse keywords — handles YAML array format: [kw1, kw2, ...]
    const kwMatch = frontmatter.match(/keywords:\s*\[([^\]]+)\]/);
    if (!kwMatch) return null;
    const keywords = kwMatch[1]
      .split(',')
      .map((k) => k.trim().toLowerCase())
      .filter(Boolean);

    if (!domain || keywords.length === 0) return null;
    return { domain, keywords, content };
  } catch {
    return null;
  }
}

function loadPresets(): Preset[] {
  if (cachedPresets) return cachedPresets;

  const dir = presetsDir();
  if (!fs.existsSync(dir)) {
    cachedPresets = [];
    return cachedPresets;
  }

  const files = fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.md'))
    .map((f) => path.join(dir, f));

  const presets: Preset[] = [];
  for (const file of files) {
    const preset = parsePreset(file);
    if (preset) presets.push(preset);
  }

  cachedPresets = presets;
  if (presets.length > 0) {
    logger.debug(
      { domains: presets.map((p) => p.domain) },
      'domain-presets: loaded %d presets',
      presets.length,
    );
  }
  return cachedPresets;
}

/**
 * Detect which domain presets match the given prompt and return their content.
 *
 * Detection is keyword-based (zero API cost, sub-millisecond).
 * A domain activates when MIN_KEYWORD_HITS distinct keywords are found.
 */
export function detectAndLoad(prompt: string): {
  domains: string[];
  presetBlock: string;
} {
  const presets = loadPresets();
  if (presets.length === 0) return { domains: [], presetBlock: '' };

  const lowerPrompt = prompt.toLowerCase();
  const matched: Preset[] = [];

  for (const preset of presets) {
    const hits = preset.keywords.filter((kw) => lowerPrompt.includes(kw));
    if (hits.length >= MIN_KEYWORD_HITS) {
      matched.push(preset);
    }
  }

  if (matched.length === 0) return { domains: [], presetBlock: '' };

  // Build content block, respecting token budget
  let combined = '';
  const activeDomains: string[] = [];

  for (const preset of matched) {
    const addition = `\n### ${preset.domain}\n${preset.content}\n`;
    if (combined.length + addition.length > MAX_PRESET_CHARS) break;
    combined += addition;
    activeDomains.push(preset.domain);
  }

  if (!combined) return { domains: [], presetBlock: '' };

  const presetBlock = `<domain-presets>\n${combined.trim()}\n</domain-presets>`;

  logger.debug(
    { domains: activeDomains },
    'domain-presets: activated %d domains',
    activeDomains.length,
  );

  return { domains: activeDomains, presetBlock };
}

/** Force reload presets from disk (useful after adding/editing preset files). */
export function reloadPresets(): void {
  cachedPresets = null;
  loadPresets();
}
