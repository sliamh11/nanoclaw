import { describe, it, expect } from 'vitest';
import { runAnalysis, type AnalysisInput } from './analysis.js';
import type {
  PortfolioState,
  MultiTFResult,
  SetupResult,
} from './types.js';
import type { RegimeInput } from './regime.js';

// Monday 12:00 PM ET — inside market hours, outside buffer
const MARKET_HOURS_TIME = new Date('2026-04-13T12:00:00-04:00');

function defaultRegimeInput(): RegimeInput {
  return {
    adx: 28,
    vix: 16,
    priceVsSma50: 2.5,
    priceVsSma200: 6.0,
    atr14: 3.5,
    atrPercent: 1.75,
    volumeRatio: 1.2,
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

function bullishMultiTF(): MultiTFResult {
  return {
    readings: [
      { tf: 'D', bias: 'bullish', keyLevel: 195, emaAlignment: true, volume: 'normal' },
      { tf: '4H', bias: 'bullish', keyLevel: 198, emaAlignment: true, volume: 'high' },
      { tf: '1H', bias: 'bullish', keyLevel: 199, emaAlignment: true, volume: 'normal' },
      { tf: '15m', bias: 'neutral', keyLevel: 200, emaAlignment: false, volume: 'low' },
    ],
    overallBias: 'bullish',
    alignment: 0.75,
    conflictingTFs: ['15m'],
  };
}

function validSetup(): SetupResult {
  return {
    type: 'breakout',
    symbol: 'AAPL',
    confluenceFactors: [
      { name: 'ema-crossover', weight: 1.0, description: 'EMA 9/21 bullish cross' },
      { name: 'volume-spike', weight: 1.0, description: '1.5x avg volume' },
      { name: 'sr-breakout', weight: 1.2, description: 'Above key resistance' },
    ],
    confluenceScore: 3.2,
    entryPrice: 200,
    stopLoss: 195,
    targets: [210, 215, 220],
    riskRewardRatio: 2.0,
  };
}

describe('runAnalysis', () => {
  it('should produce BUY for bullish trending setup', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio: defaultPortfolio(),
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      qualitativeRisk: { earningsRisk: false, macroRisk: 'none', overrideAdvice: 'proceed' },
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.symbol).toBe('AAPL');
    expect(result.regime.regime).toBe('trending-up');
    expect(result.decision.action).toBe('BUY');
    expect(result.decision.confidence).toBeGreaterThan(0.4);
    expect(result.decision.bracketOrder).not.toBeNull();
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('should produce HOLD when regime blocks trading', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: {
        ...defaultRegimeInput(),
        vix: 40, // Extreme volatility
        adx: 30,
        atrPercent: 5.0,
      },
      portfolio: defaultPortfolio(),
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.decision.action).toBe('HOLD');
  });

  it('should produce HOLD when no setup provided', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio: defaultPortfolio(),
      now: MARKET_HOURS_TIME,
      // No setup, no multiTF
    };

    const result = runAnalysis(input);
    expect(result.decision.action).toBe('HOLD');
    expect(result.decision.reasoning).toContain('No valid setup');
  });

  it('should produce HOLD when earnings risk detected', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio: defaultPortfolio(),
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      qualitativeRisk: { earningsRisk: true, macroRisk: 'none', overrideAdvice: 'proceed' },
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    // Should have earnings violation
    expect(result.risk?.violations.some((v) => v.includes('earnings'))).toBe(true);
    expect(result.decision.action).toBe('HOLD');
  });

  it('should produce HOLD when qualitative risk says abort', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio: defaultPortfolio(),
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      qualitativeRisk: { earningsRisk: false, macroRisk: 'high', overrideAdvice: 'abort' },
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.risk?.violations.some((v) => v.includes('qualitative'))).toBe(true);
    expect(result.decision.action).toBe('HOLD');
  });

  it('should include requestId and timestamp', () => {
    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio: defaultPortfolio(),
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.requestId).toBeTruthy();
    expect(() => new Date(result.timestamp)).not.toThrow();
  });

  it('should handle portfolio at max positions', () => {
    const portfolio = defaultPortfolio();
    portfolio.openPositions = Array.from({ length: 5 }, (_, i) => ({
      symbol: `SYM${i}`,
      shares: 10,
      avgCost: 100,
      currentPrice: 100,
      unrealizedPnl: 0,
    }));

    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio,
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.risk?.violations.some((v) => v.includes('max-positions'))).toBe(true);
    expect(result.decision.action).toBe('HOLD');
  });

  it('should handle daily drawdown exceeded', () => {
    const portfolio = defaultPortfolio();
    portfolio.dailyPnlPct = -3.5;

    const input: AnalysisInput = {
      symbol: 'AAPL',
      regimeIndicators: defaultRegimeInput(),
      portfolio,
      multiTF: bullishMultiTF(),
      setup: validSetup(),
      now: MARKET_HOURS_TIME,
    };

    const result = runAnalysis(input);
    expect(result.risk?.violations.some((v) => v.includes('daily-drawdown'))).toBe(true);
    expect(result.decision.action).toBe('HOLD');
  });
});
