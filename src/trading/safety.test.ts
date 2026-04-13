import { describe, it, expect } from 'vitest';
import {
  checkSafetyRails,
  calculatePositionSize,
  estimateCorrelation,
  calculatePortfolioHeat,
  checkMarketHoursBuffer,
  checkStalenessGate,
} from './safety.js';
import type {
  SafetyConfig,
  PortfolioState,
  SetupResult,
  RegimeSignal,
} from './types.js';

// --- Test fixtures ---

function defaultConfig(): SafetyConfig {
  return {
    maxRiskPerTradePct: 2,
    dailyDrawdownLimitPct: 3,
    maxOpenPositions: 5,
    maxCorrelation: 0.7,
    earningsBlackoutHours: 48,
    marketOpenBufferMin: 15,
    marketCloseBufferMin: 15,
    approvalTtlMin: 10,
    stalenessGatePct: 0.5,
    paperTradingOnly: false,
    allowedExchanges: ['NYSE', 'NASDAQ'],
  };
}

function defaultPortfolio(): PortfolioState {
  return {
    accountValue: 100000,
    cashAvailable: 80000,
    openPositions: [],
    dailyPnl: 0,
    dailyPnlPct: 0,
  };
}

function defaultSetup(): SetupResult {
  return {
    type: 'breakout',
    symbol: 'AAPL',
    confluenceFactors: [
      { name: 'ema-crossover', weight: 1.0, description: 'EMA 9/21 bullish cross' },
      { name: 'volume-spike', weight: 1.0, description: '1.5x avg volume' },
      { name: 'sr-breakout', weight: 1.2, description: 'Above resistance' },
    ],
    confluenceScore: 3.2,
    entryPrice: 200,
    stopLoss: 195,
    targets: [210, 215, 220],
    riskRewardRatio: 2.0,
  };
}

function defaultRegime(): RegimeSignal {
  return {
    regime: 'trending-up',
    confidence: 0.75,
    adx: 28,
    vix: 16,
    timestamp: new Date().toISOString(),
  };
}

// --- Safety Rails ---

// Monday 12:00 PM ET — inside market hours, outside buffer
const MARKET_HOURS_TIME = new Date('2026-04-13T12:00:00-04:00');

describe('checkSafetyRails', () => {
  it('should pass when all conditions met', () => {
    const result = checkSafetyRails(
      defaultConfig(),
      defaultPortfolio(),
      defaultSetup(),
      defaultRegime(),
      MARKET_HOURS_TIME,
    );
    expect(result.violations).toEqual([]);
    expect(result.sizing.shares).toBeGreaterThan(0);
  });

  it('should flag max positions exceeded', () => {
    const portfolio = defaultPortfolio();
    portfolio.openPositions = Array.from({ length: 5 }, (_, i) => ({
      symbol: `SYM${i}`,
      shares: 10,
      avgCost: 100,
      currentPrice: 100,
      unrealizedPnl: 0,
    }));

    const result = checkSafetyRails(
      defaultConfig(),
      portfolio,
      defaultSetup(),
      defaultRegime(),
    );
    expect(result.violations).toContain('max-positions: 5/5');
  });

  it('should flag daily drawdown breach', () => {
    const portfolio = defaultPortfolio();
    portfolio.dailyPnlPct = -3.5;

    const result = checkSafetyRails(
      defaultConfig(),
      portfolio,
      defaultSetup(),
      defaultRegime(),
    );
    expect(result.violations.some((v) => v.startsWith('daily-drawdown'))).toBe(true);
  });

  it('should flag duplicate position', () => {
    const portfolio = defaultPortfolio();
    portfolio.openPositions = [
      { symbol: 'AAPL', shares: 50, avgCost: 190, currentPrice: 200, unrealizedPnl: 500 },
    ];

    const result = checkSafetyRails(
      defaultConfig(),
      portfolio,
      defaultSetup(),
      defaultRegime(),
    );
    expect(result.violations.some((v) => v.startsWith('duplicate-position'))).toBe(true);
  });

  it('should flag low regime confidence', () => {
    const regime = defaultRegime();
    regime.confidence = 0.3;

    const result = checkSafetyRails(
      defaultConfig(),
      defaultPortfolio(),
      defaultSetup(),
      regime,
    );
    expect(result.violations.some((v) => v.startsWith('regime-confidence'))).toBe(true);
  });

  it('should flag low confluence score', () => {
    const setup = defaultSetup();
    setup.confluenceScore = 2.5;

    const result = checkSafetyRails(
      defaultConfig(),
      defaultPortfolio(),
      setup,
      defaultRegime(),
    );
    expect(result.violations.some((v) => v.startsWith('confluence'))).toBe(true);
  });

  it('should flag low R:R', () => {
    const setup = defaultSetup();
    setup.riskRewardRatio = 1.5;

    const result = checkSafetyRails(
      defaultConfig(),
      defaultPortfolio(),
      setup,
      defaultRegime(),
    );
    expect(result.violations.some((v) => v.startsWith('risk-reward'))).toBe(true);
  });

  it('should flag paper trading mode', () => {
    const config = defaultConfig();
    config.paperTradingOnly = true;

    const result = checkSafetyRails(
      config,
      defaultPortfolio(),
      defaultSetup(),
      defaultRegime(),
    );
    expect(result.violations).toContain('paper-mode: live trading disabled');
  });
});

// --- Position Sizing ---

describe('calculatePositionSize', () => {
  it('should calculate shares based on risk', () => {
    const sizing = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      defaultSetup(),
      defaultRegime(),
    );

    // $5 risk per share (200 entry, 195 stop), 2% max = $2000
    expect(sizing.shares).toBeGreaterThan(0);
    expect(sizing.shares).toBeLessThanOrEqual(400); // Max $2000 / $5 = 400
    expect(sizing.percentRisk).toBeLessThanOrEqual(2);
    expect(sizing.dollarRisk).toBeLessThanOrEqual(2000);
  });

  it('should reduce size in volatile regime', () => {
    // Use tighter stop so max cap doesn't flatten both results
    const tightSetup: SetupResult = {
      ...defaultSetup(),
      entryPrice: 200,
      stopLoss: 199, // $1 risk/share -> more shares -> cap differentiation visible
    };

    const normalRegime = defaultRegime();
    const volatileRegime: RegimeSignal = {
      ...normalRegime,
      regime: 'volatile',
      vix: 25,
    };

    const normalSize = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      tightSetup,
      normalRegime,
    );

    const volatileSize = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      tightSetup,
      volatileRegime,
    );

    // Volatile regime applies 0.5x multiplier + VIX dampening
    expect(volatileSize.adjustedFraction).toBeLessThan(normalSize.adjustedFraction);
  });

  it('should reduce size with high VIX', () => {
    const tightSetup: SetupResult = {
      ...defaultSetup(),
      entryPrice: 200,
      stopLoss: 199,
    };

    const lowVix: RegimeSignal = { ...defaultRegime(), vix: 15 };
    const highVix: RegimeSignal = { ...defaultRegime(), vix: 35 };

    const lowVixSize = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      tightSetup,
      lowVix,
    );

    const highVixSize = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      tightSetup,
      highVix,
    );

    expect(highVixSize.adjustedFraction).toBeLessThan(lowVixSize.adjustedFraction);
  });

  it('should return 0 shares for zero risk per share', () => {
    const setup = defaultSetup();
    setup.stopLoss = setup.entryPrice; // Zero risk

    const sizing = calculatePositionSize(
      defaultConfig(),
      defaultPortfolio(),
      setup,
      defaultRegime(),
    );

    expect(sizing.shares).toBe(0);
  });

  it('should return 0 shares for zero account value', () => {
    const portfolio = defaultPortfolio();
    portfolio.accountValue = 0;

    const sizing = calculatePositionSize(
      defaultConfig(),
      portfolio,
      defaultSetup(),
      defaultRegime(),
    );

    expect(sizing.shares).toBe(0);
  });
});

// --- Correlation ---

describe('estimateCorrelation', () => {
  it('should return 0 for empty positions', () => {
    expect(estimateCorrelation('AAPL', [])).toBe(0);
  });

  it('should return 1.0 for same symbol', () => {
    const positions = [
      { symbol: 'AAPL', shares: 10, avgCost: 190, currentPrice: 200, unrealizedPnl: 100 },
    ];
    expect(estimateCorrelation('AAPL', positions)).toBe(1.0);
  });

  it('should return high correlation for same sector', () => {
    const positions = [
      { symbol: 'MSFT', shares: 10, avgCost: 400, currentPrice: 410, unrealizedPnl: 100 },
    ];
    expect(estimateCorrelation('AAPL', positions)).toBeGreaterThanOrEqual(0.7);
  });

  it('should return high correlation for crypto stocks + crypto ETFs', () => {
    const positions = [
      { symbol: 'COIN', shares: 10, avgCost: 200, currentPrice: 210, unrealizedPnl: 100 },
    ];
    expect(estimateCorrelation('IBIT', positions)).toBeGreaterThanOrEqual(0.8);
  });

  it('should return lower correlation for different sectors', () => {
    const positions = [
      { symbol: 'AAPL', shares: 10, avgCost: 190, currentPrice: 200, unrealizedPnl: 100 },
    ];
    // COIN (crypto) vs AAPL (tech) = different sectors
    expect(estimateCorrelation('COIN', positions)).toBeLessThan(0.7);
  });
});

// --- Portfolio Heat ---

describe('calculatePortfolioHeat', () => {
  it('should return 0 for no positions', () => {
    expect(calculatePortfolioHeat(defaultPortfolio())).toBe(0);
  });

  it('should calculate heat from negative unrealized PnL', () => {
    const portfolio = defaultPortfolio();
    portfolio.openPositions = [
      { symbol: 'AAPL', shares: 100, avgCost: 200, currentPrice: 195, unrealizedPnl: -500 },
    ];

    const heat = calculatePortfolioHeat(portfolio);
    expect(heat).toBeCloseTo(0.5); // $500 / $100000 = 0.5%
  });

  it('should not count winning positions in heat', () => {
    const portfolio = defaultPortfolio();
    portfolio.openPositions = [
      { symbol: 'AAPL', shares: 100, avgCost: 190, currentPrice: 200, unrealizedPnl: 1000 },
    ];

    const heat = calculatePortfolioHeat(portfolio);
    expect(heat).toBe(0);
  });
});

// --- Market Hours ---

describe('checkMarketHoursBuffer', () => {
  it('should reject weekend', () => {
    // Sunday
    const sunday = new Date('2026-04-12T12:00:00-04:00');
    const result = checkMarketHoursBuffer(sunday, 15, 15);
    expect(result).toBe('market-closed: weekend');
  });

  it('should reject pre-market', () => {
    // 8:00 AM ET on a Monday
    const preMarket = new Date('2026-04-13T08:00:00-04:00');
    const result = checkMarketHoursBuffer(preMarket, 15, 15);
    expect(result).toContain('market-closed');
  });

  it('should reject first 15 minutes', () => {
    // 9:35 AM ET
    const earlyOpen = new Date('2026-04-13T09:35:00-04:00');
    const result = checkMarketHoursBuffer(earlyOpen, 15, 15);
    expect(result).toContain('market-buffer');
  });

  it('should reject last 15 minutes', () => {
    // 3:50 PM ET
    const lateClose = new Date('2026-04-13T15:50:00-04:00');
    const result = checkMarketHoursBuffer(lateClose, 15, 15);
    expect(result).toContain('market-buffer');
  });

  it('should allow mid-day trading', () => {
    // 12:00 PM ET on a Monday
    const midDay = new Date('2026-04-13T12:00:00-04:00');
    const result = checkMarketHoursBuffer(midDay, 15, 15);
    expect(result).toBeNull();
  });
});

// --- Staleness Gate ---

describe('checkStalenessGate', () => {
  it('should pass when price drift within threshold', () => {
    const result = checkStalenessGate(200, 200.5, 0.5);
    expect(result.stale).toBe(false);
    expect(result.driftPct).toBeCloseTo(0.25);
  });

  it('should fail when price drifted too much', () => {
    const result = checkStalenessGate(200, 202, 0.5);
    expect(result.stale).toBe(true);
    expect(result.driftPct).toBeCloseTo(1.0);
  });

  it('should handle downward drift', () => {
    const result = checkStalenessGate(200, 198, 0.5);
    expect(result.stale).toBe(true);
    expect(result.driftPct).toBeCloseTo(1.0);
  });
});
