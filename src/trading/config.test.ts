import { describe, it, expect, vi, afterEach } from 'vitest';
import { loadSafetyConfig } from './config.js';

afterEach(() => {
  vi.restoreAllMocks();
  // Clean up env vars
  delete process.env.TRADING_MAX_RISK_PCT;
  delete process.env.TRADING_DAILY_DRAWDOWN_PCT;
  delete process.env.TRADING_MAX_POSITIONS;
  delete process.env.TRADING_PAPER_ONLY;
  delete process.env.TRADING_ALLOWED_EXCHANGES;
  delete process.env.TRADING_MAX_CORRELATION;
  delete process.env.TRADING_EARNINGS_BLACKOUT_H;
  delete process.env.TRADING_MARKET_BUFFER_MIN;
});

describe('loadSafetyConfig', () => {
  it('should return conservative defaults', () => {
    const config = loadSafetyConfig();
    expect(config.maxRiskPerTradePct).toBe(2);
    expect(config.dailyDrawdownLimitPct).toBe(3);
    expect(config.maxOpenPositions).toBe(5);
    expect(config.paperTradingOnly).toBe(true);
    expect(config.allowedExchanges).toEqual(['NYSE', 'NASDAQ']);
    expect(config.earningsBlackoutHours).toBe(48);
    expect(config.marketOpenBufferMin).toBe(15);
    expect(config.marketCloseBufferMin).toBe(15);
    expect(config.approvalTtlMin).toBe(10);
    expect(config.stalenessGatePct).toBe(0.5);
    expect(config.maxCorrelation).toBe(0.7);
  });

  it('should not allow env to exceed hard ceilings', () => {
    process.env.TRADING_MAX_RISK_PCT = '5'; // Exceeds 2% ceiling
    process.env.TRADING_DAILY_DRAWDOWN_PCT = '10'; // Exceeds 3% ceiling
    process.env.TRADING_MAX_POSITIONS = '20'; // Exceeds 5 ceiling
    process.env.TRADING_MAX_CORRELATION = '0.95'; // Exceeds 0.7 ceiling

    const config = loadSafetyConfig();
    expect(config.maxRiskPerTradePct).toBe(2); // Capped
    expect(config.dailyDrawdownLimitPct).toBe(3); // Capped
    expect(config.maxOpenPositions).toBe(5); // Capped
    expect(config.maxCorrelation).toBe(0.7); // Capped
  });

  it('should allow env to make limits stricter', () => {
    process.env.TRADING_MAX_RISK_PCT = '1';
    process.env.TRADING_DAILY_DRAWDOWN_PCT = '1.5';
    process.env.TRADING_MAX_POSITIONS = '3';

    const config = loadSafetyConfig();
    expect(config.maxRiskPerTradePct).toBe(1);
    expect(config.dailyDrawdownLimitPct).toBe(1.5);
    expect(config.maxOpenPositions).toBe(3);
  });

  it('should not allow earnings blackout below 48h', () => {
    process.env.TRADING_EARNINGS_BLACKOUT_H = '24';
    const config = loadSafetyConfig();
    expect(config.earningsBlackoutHours).toBe(48); // Floor enforced
  });

  it('should not allow market buffer below 15min', () => {
    process.env.TRADING_MARKET_BUFFER_MIN = '5';
    const config = loadSafetyConfig();
    expect(config.marketOpenBufferMin).toBe(15); // Floor enforced
    expect(config.marketCloseBufferMin).toBe(15); // Floor enforced
  });

  it('should parse paper only from env', () => {
    process.env.TRADING_PAPER_ONLY = 'false';
    const config = loadSafetyConfig();
    expect(config.paperTradingOnly).toBe(false);
  });

  it('should parse custom exchanges', () => {
    process.env.TRADING_ALLOWED_EXCHANGES = 'NYSE, NASDAQ, AMEX';
    const config = loadSafetyConfig();
    expect(config.allowedExchanges).toEqual(['NYSE', 'NASDAQ', 'AMEX']);
  });
});
