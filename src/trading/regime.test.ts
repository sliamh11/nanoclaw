import { describe, it, expect } from 'vitest';
import {
  detectRegime,
  regimeAllowsTrading,
  getRegimeParams,
  type RegimeInput,
} from './regime.js';

function defaultInput(): RegimeInput {
  return {
    adx: 25,
    vix: 16,
    priceVsSma50: 2.0,
    priceVsSma200: 5.0,
    atr14: 3.5,
    atrPercent: 1.75,
    volumeRatio: 1.0,
  };
}

describe('detectRegime', () => {
  it('should detect trending-up with strong ADX + price above SMAs', () => {
    const input: RegimeInput = {
      ...defaultInput(),
      adx: 30,
      vix: 15,
      priceVsSma50: 3.0,
      priceVsSma200: 8.0,
    };

    const result = detectRegime(input);
    expect(result.regime).toBe('trending-up');
    expect(result.confidence).toBeGreaterThan(0.5);
  });

  it('should detect trending-down with strong ADX + price below SMAs', () => {
    const input: RegimeInput = {
      ...defaultInput(),
      adx: 30,
      vix: 18,
      priceVsSma50: -3.0,
      priceVsSma200: -5.0,
    };

    const result = detectRegime(input);
    expect(result.regime).toBe('trending-down');
    expect(result.confidence).toBeGreaterThan(0.5);
  });

  it('should detect ranging with low ADX + low VIX', () => {
    const input: RegimeInput = {
      ...defaultInput(),
      adx: 15,
      vix: 14,
      priceVsSma50: 0.5,
      priceVsSma200: 1.0,
      atrPercent: 0.8,
      volumeRatio: 0.6,
    };

    const result = detectRegime(input);
    expect(result.regime).toBe('ranging');
  });

  it('should detect volatile with VIX > 30', () => {
    const input: RegimeInput = {
      ...defaultInput(),
      adx: 28,
      vix: 35,
      atrPercent: 4.0,
    };

    const result = detectRegime(input);
    expect(result.regime).toBe('volatile');
  });

  it('should have minimum confidence of 0.3', () => {
    const result = detectRegime(defaultInput());
    expect(result.confidence).toBeGreaterThanOrEqual(0.3);
  });

  it('should include ADX and VIX in result', () => {
    const input = defaultInput();
    const result = detectRegime(input);
    expect(result.adx).toBe(input.adx);
    expect(result.vix).toBe(input.vix);
  });

  it('should include ISO timestamp', () => {
    const result = detectRegime(defaultInput());
    expect(() => new Date(result.timestamp)).not.toThrow();
  });
});

describe('regimeAllowsTrading', () => {
  it('should allow trading with sufficient confidence', () => {
    const result = regimeAllowsTrading({
      regime: 'trending-up',
      confidence: 0.75,
      adx: 28,
      vix: 16,
      timestamp: new Date().toISOString(),
    });
    expect(result.allowed).toBe(true);
  });

  it('should block trading with low confidence', () => {
    const result = regimeAllowsTrading({
      regime: 'trending-up',
      confidence: 0.4,
      adx: 22,
      vix: 18,
      timestamp: new Date().toISOString(),
    });
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('confidence');
  });

  it('should block volatile + high VIX', () => {
    const result = regimeAllowsTrading({
      regime: 'volatile',
      confidence: 0.8,
      adx: 30,
      vix: 35,
      timestamp: new Date().toISOString(),
    });
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('Volatile');
  });

  it('should allow volatile with moderate VIX', () => {
    const result = regimeAllowsTrading({
      regime: 'volatile',
      confidence: 0.6,
      adx: 25,
      vix: 25,
      timestamp: new Date().toISOString(),
    });
    expect(result.allowed).toBe(true);
  });
});

describe('getRegimeParams', () => {
  it('should return breakout/pullback for trending-up', () => {
    const params = getRegimeParams('trending-up');
    expect(params.preferredSetups).toContain('breakout');
    expect(params.preferredSetups).toContain('pullback');
    expect(params.sizingMultiplier).toBe(1.0);
  });

  it('should return higher confluence bar for trending-down', () => {
    const params = getRegimeParams('trending-down');
    expect(params.minConfluence).toBeGreaterThan(3.0);
    expect(params.sizingMultiplier).toBeLessThan(1.0);
  });

  it('should return range-bounce for ranging', () => {
    const params = getRegimeParams('ranging');
    expect(params.preferredSetups).toContain('range-bounce');
  });

  it('should return highest bar for volatile', () => {
    const params = getRegimeParams('volatile');
    expect(params.minConfluence).toBe(4.0);
    expect(params.minRR).toBe(3.0);
    expect(params.sizingMultiplier).toBe(0.5);
  });
});
