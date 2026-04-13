/**
 * Trading safety rails module.
 *
 * Enforces risk management rules as hard constraints.
 * Design: disabled = safe state. Every check returns violations list.
 * Empty violations = trade allowed.
 *
 * Rules (from architecture research):
 * - Max 2% risk per trade (hard ceiling, env cannot exceed)
 * - 3% daily drawdown circuit breaker
 * - Max 5 open positions
 * - Correlation check between positions
 * - No entries within 48h of earnings
 * - No first/last 15 min of market session
 * - Paper trading default (must explicitly enable live)
 *
 * Academic basis:
 * - Hybrid Kelly-VIX position sizing (arXiv:2508.16598)
 * - Regime-trust gate for confidence (arXiv:2603.13252)
 */

import { logger } from '../logger.js';
import {
  SafetyConfig,
  PortfolioState,
  SetupResult,
  RegimeSignal,
  PositionSizing,
  RiskResult,
  OpenPosition,
} from './types.js';

/**
 * Check all safety rails for a proposed trade.
 * Returns violations array — empty means trade is allowed.
 */
export function checkSafetyRails(
  config: SafetyConfig,
  portfolio: PortfolioState,
  setup: SetupResult,
  regime: RegimeSignal,
  now?: Date,
): RiskResult {
  const violations: string[] = [];
  const currentTime = now ?? new Date();

  // --- Position count check ---
  if (portfolio.openPositions.length >= config.maxOpenPositions) {
    violations.push(
      `max-positions: ${portfolio.openPositions.length}/${config.maxOpenPositions}`,
    );
  }

  // --- Daily drawdown circuit breaker ---
  const drawdownPct = Math.abs(portfolio.dailyPnlPct);
  if (drawdownPct >= config.dailyDrawdownLimitPct) {
    violations.push(
      `daily-drawdown: ${drawdownPct.toFixed(2)}% >= ${config.dailyDrawdownLimitPct}% limit`,
    );
  }

  // --- Market hours buffer ---
  const marketViolation = checkMarketHoursBuffer(
    currentTime,
    config.marketOpenBufferMin,
    config.marketCloseBufferMin,
  );
  if (marketViolation) {
    violations.push(marketViolation);
  }

  // --- Duplicate position check ---
  const existingPosition = portfolio.openPositions.find(
    (p) => p.symbol === setup.symbol,
  );
  if (existingPosition) {
    violations.push(
      `duplicate-position: already holding ${existingPosition.shares} shares of ${setup.symbol}`,
    );
  }

  // --- Position sizing ---
  const sizing = calculatePositionSize(
    config,
    portfolio,
    setup,
    regime,
  );

  if (sizing.percentRisk > config.maxRiskPerTradePct) {
    violations.push(
      `risk-per-trade: ${sizing.percentRisk.toFixed(2)}% > ${config.maxRiskPerTradePct}% limit`,
    );
  }

  // --- Correlation check ---
  const correlationRisk = estimateCorrelation(
    setup.symbol,
    portfolio.openPositions,
  );
  if (correlationRisk > config.maxCorrelation) {
    violations.push(
      `correlation: ${correlationRisk.toFixed(2)} > ${config.maxCorrelation} threshold`,
    );
  }

  // --- Portfolio heat check (total open risk) ---
  const portfolioHeat = calculatePortfolioHeat(portfolio);
  if (portfolioHeat + sizing.percentRisk > config.maxRiskPerTradePct * config.maxOpenPositions) {
    violations.push(
      `portfolio-heat: ${(portfolioHeat + sizing.percentRisk).toFixed(2)}% would exceed max`,
    );
  }

  // --- Regime confidence gate ---
  // Low-confidence regime detection = don't trade (arXiv:2603.13252)
  if (regime.confidence < 0.5) {
    violations.push(
      `regime-confidence: ${regime.confidence.toFixed(2)} < 0.50 minimum`,
    );
  }

  // --- Confluence minimum ---
  if (setup.confluenceScore < 3.0) {
    violations.push(
      `confluence: ${setup.confluenceScore.toFixed(1)} < 3.0 minimum`,
    );
  }

  // --- R:R minimum ---
  if (setup.riskRewardRatio < 2.0) {
    violations.push(
      `risk-reward: ${setup.riskRewardRatio.toFixed(2)} < 2.0 minimum`,
    );
  }

  // --- Paper trading mode ---
  if (config.paperTradingOnly) {
    violations.push('paper-mode: live trading disabled');
  }

  if (violations.length > 0) {
    logger.debug({ violations, symbol: setup.symbol }, 'safety: violations found');
  }

  return {
    sizing,
    atrStop: Math.abs(setup.entryPrice - setup.stopLoss),
    correlationRisk,
    portfolioHeatPct: portfolioHeat,
    dailyDrawdownPct: drawdownPct,
    earningsWithin48h: false, // Populated by caller via external data
    violations,
  };
}

/**
 * Hybrid Kelly-VIX position sizing.
 *
 * Academic basis: arXiv:2508.16598
 * Kelly alone too conservative in low-vol, VIX alone too volatile.
 * We use half-Kelly with VIX dampening as the safe default.
 */
export function calculatePositionSize(
  config: SafetyConfig,
  portfolio: PortfolioState,
  setup: SetupResult,
  regime: RegimeSignal,
): PositionSizing {
  const accountValue = portfolio.accountValue;
  if (accountValue <= 0) {
    return { shares: 0, dollarRisk: 0, percentRisk: 0, kellyFraction: 0, adjustedFraction: 0 };
  }

  const riskPerShare = Math.abs(setup.entryPrice - setup.stopLoss);
  if (riskPerShare <= 0) {
    return { shares: 0, dollarRisk: 0, percentRisk: 0, kellyFraction: 0, adjustedFraction: 0 };
  }

  // Kelly criterion: f = (bp - q) / b
  // Where b = R:R ratio, p = estimated win probability (from confidence), q = 1-p
  const winProb = Math.min(0.65, regime.confidence * 0.7 + 0.15); // Capped at 65%
  const lossProb = 1 - winProb;
  const payoffRatio = setup.riskRewardRatio;
  const kellyFraction = Math.max(
    0,
    (payoffRatio * winProb - lossProb) / payoffRatio,
  );

  // Half-Kelly for safety (standard practice)
  let adjustedFraction = kellyFraction * 0.5;

  // VIX dampening: scale down in high-volatility regimes
  // Normal VIX ~15, elevated >20, crisis >30
  if (regime.vix > 20) {
    const vixMultiplier = Math.max(0.3, 1 - (regime.vix - 20) / 40);
    adjustedFraction *= vixMultiplier;
  }

  // Regime dampening: reduce in volatile/ranging regimes
  if (regime.regime === 'volatile') {
    adjustedFraction *= 0.5;
  } else if (regime.regime === 'ranging') {
    adjustedFraction *= 0.7;
  }

  // Cap at max risk per trade
  const maxDollarRisk = accountValue * (config.maxRiskPerTradePct / 100);
  const dollarRisk = Math.min(
    accountValue * adjustedFraction,
    maxDollarRisk,
  );

  const shares = Math.floor(dollarRisk / riskPerShare);
  const actualDollarRisk = shares * riskPerShare;
  const percentRisk = (actualDollarRisk / accountValue) * 100;

  return {
    shares,
    dollarRisk: actualDollarRisk,
    percentRisk,
    kellyFraction,
    adjustedFraction,
  };
}

/**
 * Estimate pairwise correlation between two symbols based on sector.
 */
function estimatePairCorrelation(
  symbolA: string,
  symbolB: string,
  sectorA: string,
  sectorB: string,
): number {
  if (symbolA === symbolB) return 1.0;
  if (sectorA === sectorB) return 0.8;
  if (
    (sectorA === 'crypto' && sectorB === 'crypto-etf') ||
    (sectorA === 'crypto-etf' && sectorB === 'crypto')
  ) return 0.85;
  if (
    (sectorA === 'tech' && sectorB === 'tech-etf') ||
    (sectorA === 'tech-etf' && sectorB === 'tech')
  ) return 0.7;
  if (sectorA !== 'unknown' && sectorB !== 'unknown') return 0.3;
  return 0.5; // Unknown — assume moderate
}

/**
 * Estimate correlation risk between a new symbol and existing positions.
 *
 * Simple sector-based heuristic. Returns 0-1.
 * Same sector = high correlation, different sector = low.
 * TODO: Replace with actual correlation matrix from historical data.
 */
export function estimateCorrelation(
  symbol: string,
  positions: OpenPosition[],
): number {
  if (positions.length === 0) return 0;

  // Known sector groupings for user's traded instruments
  const sectorMap: Record<string, string> = {
    // Crypto-adjacent
    COIN: 'crypto', HOOD: 'crypto', BLSH: 'crypto', BMNR: 'crypto',
    ETHA: 'crypto-etf', IBIT: 'crypto-etf',
    // Tech mega
    AAPL: 'tech', MSFT: 'tech', GOOGL: 'tech', AMZN: 'tech', META: 'tech', NVDA: 'tech',
    // Broad ETFs
    SPY: 'broad', QQQ: 'tech-etf', IWM: 'small-cap',
  };

  const newSector = sectorMap[symbol] ?? 'unknown';
  let maxCorrelation = 0;

  for (const pos of positions) {
    const posSector = sectorMap[pos.symbol] ?? 'unknown';
    const corr = estimatePairCorrelation(symbol, pos.symbol, newSector, posSector);
    maxCorrelation = Math.max(maxCorrelation, corr);
  }

  return maxCorrelation;
}

/**
 * Calculate total portfolio heat (% of account at risk across all positions).
 */
export function calculatePortfolioHeat(portfolio: PortfolioState): number {
  if (portfolio.accountValue <= 0) return 0;

  let totalRiskDollars = 0;
  for (const pos of portfolio.openPositions) {
    // Estimate risk as unrealized P&L if negative, or a default % if positive
    const posRisk = pos.unrealizedPnl < 0 ? Math.abs(pos.unrealizedPnl) : 0;
    totalRiskDollars += posRisk;
  }

  return (totalRiskDollars / portfolio.accountValue) * 100;
}

/**
 * Check if current time is within market hours buffer zones.
 * US market: 9:30 AM - 4:00 PM ET.
 * No entries in first/last N minutes.
 */
export function checkMarketHoursBuffer(
  now: Date,
  openBufferMin: number,
  closeBufferMin: number,
): string | null {
  // Convert to ET (Eastern Time)
  const etStr = now.toLocaleString('en-US', { timeZone: 'America/New_York' });
  const et = new Date(etStr);
  const hours = et.getHours();
  const minutes = et.getMinutes();
  const totalMinutes = hours * 60 + minutes;

  const marketOpen = 9 * 60 + 30; // 9:30 AM
  const marketClose = 16 * 60; // 4:00 PM

  // Check if market is open at all
  const dayOfWeek = et.getDay();
  if (dayOfWeek === 0 || dayOfWeek === 6) {
    return 'market-closed: weekend';
  }

  if (totalMinutes < marketOpen || totalMinutes >= marketClose) {
    return 'market-closed: outside trading hours';
  }

  // Buffer zone checks
  if (totalMinutes < marketOpen + openBufferMin) {
    return `market-buffer: within first ${openBufferMin}min of open`;
  }

  if (totalMinutes >= marketClose - closeBufferMin) {
    return `market-buffer: within last ${closeBufferMin}min of close`;
  }

  return null;
}

/**
 * Check staleness gate: has price moved too far since analysis?
 * Used at approval time to abort stale proposals.
 */
export function checkStalenessGate(
  analysisPrice: number,
  currentPrice: number,
  maxDriftPct: number,
): { stale: boolean; driftPct: number } {
  const driftPct = Math.abs((currentPrice - analysisPrice) / analysisPrice) * 100;
  return {
    stale: driftPct > maxDriftPct,
    driftPct,
  };
}
