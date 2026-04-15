/**
 * Step: ollama — Enforce Ollama as a hard requirement, kick off background
 * downloads for every required model, and return immediately.
 *
 * Deus depends on Ollama for local embeddings (memory-tree) and the default
 * judge model. When this step runs:
 *   1. If the `ollama` CLI is missing → fail setup with install instructions.
 *   2. Compute the required-model list (embedder + hardware-recommended judge).
 *   3. Check which are already pulled; skip those.
 *   4. For each missing model, spawn a detached `ollama pull` that writes
 *      progress to a per-model log, then return immediately.
 *
 * Downloads continue in the background after `deus setup` exits. Users can
 * tail the log files under ~/.config/deus/ollama-downloads/ to monitor.
 */
import { execSync, spawn, spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';

import { CONFIG_DIR } from '../src/config.js';
import { logger } from '../src/logger.js';
import { commandExists } from './platform.js';
import { emitStatus } from './status.js';

const DOWNLOAD_DIR = path.join(CONFIG_DIR, 'ollama-downloads');
const OLLAMA_INSTALL_URL = 'https://ollama.ai/download';

/** The embedder is required by memory-tree and has no alternative. */
const EMBEDDER_MODEL = 'embeddinggemma';

interface HardwareInfo {
  os: string;
  arch: string;
  ram_gb: number;
  cores: number;
  gpu: string;
}

interface HardwareReport {
  hardware: HardwareInfo;
  recommendation: { model: string; size_gb: number; fits: boolean };
}

interface PullResult {
  model: string;
  status: 'already_present' | 'started' | 'failed';
  pid?: number;
  log?: string;
  reason?: string;
}

/** Read a single KEY=value from a .env file, return undefined if not present. */
function readEnvKey(envPath: string, key: string): string | undefined {
  if (!fs.existsSync(envPath)) return undefined;
  const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith(`${key}=`)) {
      return trimmed.slice(key.length + 1).trim();
    }
  }
  return undefined;
}

/** Write or update a single KEY=value in a .env file, preserving other lines. */
function writeEnvKey(envPath: string, key: string, value: string): void {
  const dir = path.dirname(envPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  let content = '';
  if (fs.existsSync(envPath)) {
    const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
    content = lines
      .filter((l) => !l.trim().startsWith(`${key}=`))
      .join('\n');
    if (content.length > 0 && !content.endsWith('\n')) content += '\n';
  }
  content += `${key}=${value}\n`;
  fs.writeFileSync(envPath, content, 'utf-8');
}

/** Check if a model is already pulled by scanning `ollama list` output. */
export function isModelPulled(modelName: string): boolean {
  try {
    const result = execSync('ollama list', { encoding: 'utf-8' });
    const baseName = modelName.split(':')[0];
    const tag = modelName.includes(':') ? modelName.split(':')[1] : '';
    return result.split('\n').some((line) => {
      const col = line.trim().split(/\s+/)[0];
      if (!col) return false;
      if (tag) return col === modelName || col.startsWith(modelName);
      return col === baseName || col.startsWith(`${baseName}:`);
    });
  } catch {
    return false;
  }
}

/** Normalize a model name for filesystem use (`gemma4:e4b` → `gemma4_e4b`). */
export function sanitizeModelName(name: string): string {
  return name.replace(/[^a-zA-Z0-9_.-]/g, '_');
}

/** Invoke `python3 -m evolution.hardware` and parse the JSON output. */
function getHardwareReport(): HardwareReport | null {
  try {
    const result = spawnSync('python3', ['-m', 'evolution.hardware'], {
      encoding: 'utf-8',
      cwd: process.cwd(),
      env: process.env,
    });
    if (result.status !== 0 || !result.stdout) {
      logger.warn(
        { stderr: result.stderr },
        'evolution.hardware module not available — skipping hardware detection',
      );
      return null;
    }
    return JSON.parse(result.stdout.trim()) as HardwareReport;
  } catch {
    return null;
  }
}

/**
 * Spawn `ollama pull` detached. Returns the PID; the process continues after
 * this function returns and the parent exits.
 */
export function startBackgroundPull(model: string): { pid: number; logPath: string } {
  if (!fs.existsSync(DOWNLOAD_DIR)) {
    fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
  }
  const stem = sanitizeModelName(model);
  const logPath = path.join(DOWNLOAD_DIR, `${stem}.log`);
  const pidPath = path.join(DOWNLOAD_DIR, `${stem}.pid`);

  const out = fs.openSync(logPath, 'a');
  const child = spawn('ollama', ['pull', model], {
    detached: true,
    stdio: ['ignore', out, out],
  });
  fs.writeFileSync(pidPath, String(child.pid ?? ''), 'utf-8');
  child.unref();
  return { pid: child.pid ?? 0, logPath };
}

/** Compute the full list of required models for this install. */
export function computeRequiredModels(judgeModel: string | null): string[] {
  const models = new Set<string>([EMBEDDER_MODEL]);
  if (judgeModel) models.add(judgeModel);
  return [...models];
}

export async function run(_args: string[]): Promise<void> {
  // ── 1. Ollama CLI is required ──────────────────────────────────────────────
  if (!commandExists('ollama')) {
    const msg =
      `Ollama is required for Deus (local embeddings + judge). ` +
      `Install from ${OLLAMA_INSTALL_URL} and re-run \`deus setup\`.`;
    logger.error(msg);
    emitStatus('OLLAMA', {
      STATUS: 'failed',
      REASON: 'ollama_not_installed',
      INSTALL_URL: OLLAMA_INSTALL_URL,
    });
    throw new Error(msg);
  }

  // ── 2. Hardware-driven judge recommendation (best-effort) ──────────────────
  const report = getHardwareReport();
  const judgeModel = report?.recommendation.model ?? null;
  const required = computeRequiredModels(judgeModel);

  logger.info({ required, judgeModel }, 'Ollama required-model list resolved');

  // ── 3. Check which are present; kick off background pulls for the rest ────
  const results: PullResult[] = [];
  for (const model of required) {
    if (isModelPulled(model)) {
      results.push({ model, status: 'already_present' });
      continue;
    }
    try {
      const { pid, logPath } = startBackgroundPull(model);
      results.push({ model, status: 'started', pid, log: logPath });
      logger.info({ model, pid, log: logPath }, 'Background ollama pull started');
    } catch (err) {
      const reason = (err as Error).message ?? 'spawn_failed';
      results.push({ model, status: 'failed', reason });
      logger.warn({ model, reason }, 'Failed to start background ollama pull');
    }
  }

  // ── 4. Persist the recommended judge model to .env (if any) ────────────────
  if (judgeModel) {
    const envPath = path.join(CONFIG_DIR, '.env');
    const existing = readEnvKey(envPath, 'OLLAMA_MODEL');
    if (existing !== judgeModel) writeEnvKey(envPath, 'OLLAMA_MODEL', judgeModel);
  }

  // ── 5. Emit status; setup returns immediately. ─────────────────────────────
  const started = results.filter((r) => r.status === 'started');
  const present = results.filter((r) => r.status === 'already_present');

  if (started.length > 0) {
    console.log(
      `\nStarted ${started.length} Ollama model download(s) in the background. ` +
        `Progress logs in ${DOWNLOAD_DIR}/\n`,
    );
    for (const r of started) {
      console.log(`  - ${r.model}  (pid ${r.pid}, log: ${r.log})`);
    }
    console.log('');
  }

  emitStatus('OLLAMA', {
    STATUS: 'success',
    REQUIRED_COUNT: required.length,
    ALREADY_PRESENT: present.length,
    STARTED: started.length,
    RECOMMENDED_JUDGE: judgeModel ?? 'none',
    LOG_DIR: DOWNLOAD_DIR,
  });
}
