/**
 * Regime detection module.
 *
 * Classifies market state: trending-up, trending-down, ranging, volatile.
 * Uses ADX + VIX as primary signals with HMM-inspired state transitions.
 *
 * Academic basis:
 * - HMM+NN dual-agreement: 83% return over COVID (arXiv:2407.19858)
 * - HMM regime labels: ~75% accuracy OOS (MDPI 2020)
 * - Regime-trust gate: AUROC ~0.72, binary not continuous (arXiv:2603.13252)
 * - Regime-gating is non-optional — LLMs fail without it (FINSABER, KDD 2026)
 *
 * Implementation: heuristic classifier from indicator values.
 * Full HMM training deferred to Phase 2 (needs historical data pipeline).
 */

import { MarketRegime, RegimeSignal } from './types.js';

/** ADX thresholds for trend strength classification. */
const ADX_STRONG_TREND = 25;
const ADX_WEAK_TREND = 20;

/** VIX thresholds for volatility classification. */
const VIX_ELEVATED = 20;
const VIX_HIGH = 30;

/** Minimum data points for regime confidence. */
const MIN_CONFIDENCE = 0.3;

export interface RegimeInput {
  adx: number; // Average Directional Index (0-100)
  vix: number; // CBOE Volatility Index
  priceVsSma50: number; // % above/below 50-period SMA
  priceVsSma200: number; // % above/below 200-period SMA
  atr14: number; // 14-period ATR
  atrPercent: number; // ATR as % of price
  volumeRatio: number; // Current volume / 20-day avg volume
}

/**
 * Classify market regime from indicator values.
 *
 * Decision tree (research-backed order of priority):
 * 1. VIX > 30 → volatile (regardless of trend)
 * 2. ADX > 25 + price > SMA50 → trending-up
 * 3. ADX > 25 + price < SMA50 → trending-down
 * 4. ADX < 20 + VIX < 20 → ranging
 * 5. Edge cases → weighted scoring
 *
 * Confidence = weighted agreement of all signals.
 * Binary regime gate at 0.5 confidence (arXiv:2603.13252).
 */
export function detectRegime(input: RegimeInput): RegimeSignal {
  const { adx, vix, priceVsSma50, priceVsSma200, atrPercent, volumeRatio } = input;

  // Score each regime
  const scores: Record<MarketRegime, number> = {
    'trending-up': 0,
    'trending-down': 0,
    'ranging': 0,
    'volatile': 0,
  };

  // --- VIX scoring ---
  if (vix > VIX_HIGH) {
    scores['volatile'] += 3;
  } else if (vix > VIX_ELEVATED) {
    scores['volatile'] += 1;
  } else {
    scores['ranging'] += 0.5;
  }

  // --- ADX scoring (trend strength) ---
  if (adx > ADX_STRONG_TREND) {
    if (priceVsSma50 > 0) {
      scores['trending-up'] += 2;
    } else {
      scores['trending-down'] += 2;
    }
  } else if (adx > ADX_WEAK_TREND) {
    if (priceVsSma50 > 0) {
      scores['trending-up'] += 1;
    } else {
      scores['trending-down'] += 1;
    }
  } else {
    scores['ranging'] += 2;
  }

  // --- SMA alignment scoring ---
  if (priceVsSma50 > 0 && priceVsSma200 > 0) {
    scores['trending-up'] += 1.5;
  } else if (priceVsSma50 < 0 && priceVsSma200 < 0) {
    scores['trending-down'] += 1.5;
  } else {
    // Divergence between SMAs = transition / ranging
    scores['ranging'] += 1;
  }

  // --- ATR scoring (realized volatility) ---
  if (atrPercent > 3) {
    scores['volatile'] += 1.5;
  } else if (atrPercent > 2) {
    scores['volatile'] += 0.5;
  } else if (atrPercent < 1) {
    scores['ranging'] += 0.5;
  }

  // --- Volume confirmation ---
  if (volumeRatio > 1.5) {
    // High volume confirms whatever direction we're seeing
    if (priceVsSma50 > 0) scores['trending-up'] += 0.5;
    else scores['trending-down'] += 0.5;
  } else if (volumeRatio < 0.7) {
    // Low volume favors ranging
    scores['ranging'] += 0.5;
  }

  // --- Pick winner ---
  let bestRegime: MarketRegime = 'ranging';
  let bestScore = 0;
  let totalScore = 0;

  for (const [regime, score] of Object.entries(scores)) {
    totalScore += score;
    if (score > bestScore) {
      bestScore = score;
      bestRegime = regime as MarketRegime;
    }
  }

  // Confidence = winner's share of total score
  const confidence = totalScore > 0
    ? Math.max(MIN_CONFIDENCE, bestScore / totalScore)
    : MIN_CONFIDENCE;

  return {
    regime: bestRegime,
    confidence: Math.round(confidence * 100) / 100,
    adx,
    vix,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Check if regime allows trading.
 *
 * From research: binary gate at 0.5 confidence (arXiv:2603.13252).
 * Volatile regime with VIX > 30 = no new entries.
 */
export function regimeAllowsTrading(regime: RegimeSignal): {
  allowed: boolean;
  reason: string;
} {
  if (regime.confidence < 0.5) {
    return {
      allowed: false,
      reason: `Low regime confidence: ${regime.confidence} < 0.50`,
    };
  }

  if (regime.regime === 'volatile' && regime.vix > VIX_HIGH) {
    return {
      allowed: false,
      reason: `Volatile regime + VIX ${regime.vix} > ${VIX_HIGH}`,
    };
  }

  return { allowed: true, reason: 'ok' };
}

/**
 * Get regime-specific trading parameters.
 * Adjusts strategy based on detected regime.
 */
export function getRegimeParams(regime: MarketRegime): {
  preferredSetups: string[];
  minConfluence: number;
  minRR: number;
  sizingMultiplier: number;
} {
  switch (regime) {
    case 'trending-up':
      return {
        preferredSetups: ['breakout', 'pullback', 'momentum'],
        minConfluence: 3.0,
        minRR: 2.0,
        sizingMultiplier: 1.0,
      };
    case 'trending-down':
      return {
        preferredSetups: ['pullback', 'reversal'],
        minConfluence: 3.5, // Higher bar for counter-trend
        minRR: 2.5,
        sizingMultiplier: 0.7,
      };
    case 'ranging':
      return {
        preferredSetups: ['range-bounce', 'reversal'],
        minConfluence: 3.0,
        minRR: 2.0,
        sizingMultiplier: 0.8,
      };
    case 'volatile':
      return {
        preferredSetups: ['pullback'], // Only high-confidence setups
        minConfluence: 4.0,
        minRR: 3.0,
        sizingMultiplier: 0.5,
      };
  }
}
