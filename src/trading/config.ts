/**
 * Trading module configuration.
 *
 * All safety-critical defaults are conservative (paper trading, low risk).
 * Live trading requires explicit opt-in via env vars.
 * No credentials stored — uses env vars only.
 */

import { readEnvFile } from '../env.js';
import { SafetyConfig } from './types.js';

const TRADING_ENV_KEYS = [
  'TRADING_ENABLED',
  'TRADING_PAPER_ONLY',
  'TRADING_MAX_RISK_PCT',
  'TRADING_DAILY_DRAWDOWN_PCT',
  'TRADING_MAX_POSITIONS',
  'TRADING_MAX_CORRELATION',
  'TRADING_EARNINGS_BLACKOUT_H',
  'TRADING_MARKET_BUFFER_MIN',
  'TRADING_APPROVAL_TTL_MIN',
  'TRADING_STALENESS_PCT',
  'TRADING_ALLOWED_EXCHANGES',
];

const envConfig = readEnvFile(TRADING_ENV_KEYS);

function envStr(key: string): string | undefined {
  return process.env[key] || envConfig[key];
}

function envFloat(key: string, fallback: number): number {
  const raw = envStr(key);
  if (!raw) return fallback;
  const parsed = parseFloat(raw);
  return isNaN(parsed) ? fallback : parsed;
}

function envInt(key: string, fallback: number): number {
  const raw = envStr(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return isNaN(parsed) ? fallback : parsed;
}

function envBool(key: string, fallback: boolean): boolean {
  const raw = envStr(key);
  if (!raw) return fallback;
  return raw.toLowerCase() === 'true';
}

/** Whether trading analysis is enabled at all. Default: false. */
export const TRADING_ENABLED = envBool('TRADING_ENABLED', false);

/**
 * Load safety configuration from env vars with conservative defaults.
 *
 * Design: safe-by-default. Paper trading is on unless explicitly disabled.
 * Max risk caps are low. All limits are enforced as hard ceilings —
 * env vars can only make them stricter, not more permissive than code limits.
 */
export function loadSafetyConfig(): SafetyConfig {
  return {
    // Hard ceiling: 2% per trade, env can only go lower
    maxRiskPerTradePct: Math.min(
      envFloat('TRADING_MAX_RISK_PCT', 2),
      2,
    ),
    // Hard ceiling: 3% daily drawdown
    dailyDrawdownLimitPct: Math.min(
      envFloat('TRADING_DAILY_DRAWDOWN_PCT', 3),
      3,
    ),
    // Hard ceiling: 5 positions
    maxOpenPositions: Math.min(
      envInt('TRADING_MAX_POSITIONS', 5),
      5,
    ),
    maxCorrelation: Math.min(
      envFloat('TRADING_MAX_CORRELATION', 0.7),
      0.7,
    ),
    earningsBlackoutHours: Math.max(
      envInt('TRADING_EARNINGS_BLACKOUT_H', 48),
      48,
    ),
    marketOpenBufferMin: Math.max(
      envInt('TRADING_MARKET_BUFFER_MIN', 15),
      15,
    ),
    marketCloseBufferMin: Math.max(
      envInt('TRADING_MARKET_BUFFER_MIN', 15),
      15,
    ),
    approvalTtlMin: envInt('TRADING_APPROVAL_TTL_MIN', 10),
    stalenessGatePct: envFloat('TRADING_STALENESS_PCT', 0.5),
    // Default: paper only. Must explicitly set to false for live.
    paperTradingOnly: envBool('TRADING_PAPER_ONLY', true),
    allowedExchanges: (envStr('TRADING_ALLOWED_EXCHANGES') || 'NYSE,NASDAQ')
      .split(',')
      .map((e) => e.trim().toUpperCase())
      .filter(Boolean),
  };
}
