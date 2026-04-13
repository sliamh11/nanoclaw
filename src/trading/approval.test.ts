import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  formatApprovalMessage,
  createApprovalRequest,
  isApprovalExpired,
  parseApprovalResponse,
  buildBracketOrder,
  formatForIBKR,
} from './approval.js';
import type { TradeDecision, SafetyConfig, BracketOrder } from './types.js';

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

function defaultDecision(): TradeDecision {
  return {
    action: 'BUY',
    confidence: 0.72,
    symbol: 'AAPL',
    regime: 'trending-up',
    setup: {
      type: 'breakout',
      symbol: 'AAPL',
      confluenceFactors: [
        { name: 'ema-cross', weight: 1.0, description: 'EMA cross' },
        { name: 'volume', weight: 1.0, description: 'Volume spike' },
        { name: 'sr-break', weight: 1.2, description: 'S/R breakout' },
      ],
      confluenceScore: 3.2,
      entryPrice: 200,
      stopLoss: 195,
      targets: [210, 215, 220],
      riskRewardRatio: 2.0,
    },
    risk: {
      sizing: { shares: 100, dollarRisk: 500, percentRisk: 0.5, kellyFraction: 0.08, adjustedFraction: 0.04 },
      atrStop: 5,
      correlationRisk: 0.3,
      portfolioHeatPct: 1.2,
      dailyDrawdownPct: 0.5,
      earningsWithin48h: false,
      violations: [],
    },
    reasoning: 'Breakout setup, trending-up regime, bullish bias.',
    bracketOrder: {
      side: 'BUY',
      symbol: 'AAPL',
      quantity: 100,
      entryType: 'LMT',
      entryPrice: 200,
      stopLoss: 195,
      takeProfit: 210,
      timeInForce: 'DAY',
    },
    expiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    stalenessGatePricePct: 0.5,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('formatApprovalMessage', () => {
  it('should format BUY proposal', () => {
    const msg = formatApprovalMessage(defaultDecision(), defaultConfig());
    expect(msg).toContain('TRADE PROPOSAL');
    expect(msg).toContain('BUY AAPL');
    expect(msg).toContain('Entry: $200.00');
    expect(msg).toContain('Stop:  $195.00');
    expect(msg).toContain('T1:    $210.00');
    expect(msg).toContain('Reply APPROVE or REJECT');
  });

  it('should tag paper mode', () => {
    const config = defaultConfig();
    config.paperTradingOnly = true;
    const msg = formatApprovalMessage(defaultDecision(), config);
    expect(msg).toContain('[PAPER]');
  });

  it('should format HOLD message', () => {
    const decision: TradeDecision = {
      ...defaultDecision(),
      action: 'HOLD',
      bracketOrder: null,
      risk: {
        ...defaultDecision().risk!,
        violations: ['regime-confidence: 0.35 < 0.50 minimum'],
      },
    };
    const msg = formatApprovalMessage(decision, defaultConfig());
    expect(msg).toContain('HOLD');
    expect(msg).toContain('regime-confidence');
    expect(msg).not.toContain('Reply APPROVE');
  });
});

describe('createApprovalRequest', () => {
  it('should create request with correct TTL', () => {
    const request = createApprovalRequest(defaultDecision(), 'whatsapp', 10);
    expect(request.status).toBe('pending');
    expect(request.channel).toBe('whatsapp');

    const sentAt = new Date(request.sentAt).getTime();
    const expiresAt = new Date(request.expiresAt).getTime();
    expect(expiresAt - sentAt).toBe(10 * 60 * 1000);
  });

  it('should generate unique request IDs', () => {
    const req1 = createApprovalRequest(defaultDecision(), 'whatsapp', 10);
    const req2 = createApprovalRequest(defaultDecision(), 'telegram', 10);
    expect(req1.requestId).not.toBe(req2.requestId);
  });
});

describe('isApprovalExpired', () => {
  it('should return false for active request', () => {
    const request = createApprovalRequest(defaultDecision(), 'whatsapp', 10);
    expect(isApprovalExpired(request)).toBe(false);
  });

  it('should return true for expired request', () => {
    const request = createApprovalRequest(defaultDecision(), 'whatsapp', 10);
    request.expiresAt = new Date(Date.now() - 1000).toISOString();
    expect(isApprovalExpired(request)).toBe(true);
  });
});

describe('parseApprovalResponse', () => {
  it('should parse approve variants', () => {
    expect(parseApprovalResponse('approve')).toBe('approved');
    expect(parseApprovalResponse('APPROVED')).toBe('approved');
    expect(parseApprovalResponse('yes')).toBe('approved');
    expect(parseApprovalResponse('go')).toBe('approved');
    expect(parseApprovalResponse('execute')).toBe('approved');
    expect(parseApprovalResponse('ok')).toBe('approved');
  });

  it('should parse reject variants', () => {
    expect(parseApprovalResponse('reject')).toBe('rejected');
    expect(parseApprovalResponse('REJECTED')).toBe('rejected');
    expect(parseApprovalResponse('no')).toBe('rejected');
    expect(parseApprovalResponse('cancel')).toBe('rejected');
    expect(parseApprovalResponse('abort')).toBe('rejected');
    expect(parseApprovalResponse('skip')).toBe('rejected');
  });

  it('should return null for non-approval messages', () => {
    expect(parseApprovalResponse('hello')).toBeNull();
    expect(parseApprovalResponse('what do you think?')).toBeNull();
    expect(parseApprovalResponse('')).toBeNull();
  });

  it('should handle whitespace', () => {
    expect(parseApprovalResponse('  approve  ')).toBe('approved');
    expect(parseApprovalResponse(' reject ')).toBe('rejected');
  });
});

describe('buildBracketOrder', () => {
  it('should build bracket from BUY decision', () => {
    const order = buildBracketOrder(defaultDecision());
    expect(order).not.toBeNull();
    expect(order!.side).toBe('BUY');
    expect(order!.symbol).toBe('AAPL');
    expect(order!.quantity).toBe(100);
    expect(order!.entryPrice).toBe(200);
    expect(order!.stopLoss).toBe(195);
    expect(order!.takeProfit).toBe(210);
  });

  it('should return null for HOLD', () => {
    const decision = { ...defaultDecision(), action: 'HOLD' as const };
    expect(buildBracketOrder(decision)).toBeNull();
  });

  it('should return null for zero shares', () => {
    const decision = defaultDecision();
    decision.risk!.sizing.shares = 0;
    expect(buildBracketOrder(decision)).toBeNull();
  });
});

describe('formatForIBKR', () => {
  it('should format bracket order for IBKR MCP', () => {
    const order: BracketOrder = {
      side: 'BUY',
      symbol: 'AAPL',
      quantity: 100,
      entryType: 'LMT',
      entryPrice: 200,
      stopLoss: 195,
      takeProfit: 210,
      timeInForce: 'DAY',
    };

    const ibkrParams = formatForIBKR(order);
    expect(ibkrParams.symbol).toBe('AAPL');
    expect(ibkrParams.side).toBe('BUY');
    expect(ibkrParams.quantity).toBe(100);
    expect(ibkrParams.orderType).toBe('LMT');
    expect(ibkrParams.price).toBe(200);
    expect(ibkrParams.stopPrice).toBe(195);
    expect(ibkrParams.takeProfitPrice).toBe(210);
    expect(ibkrParams.tif).toBe('DAY');
  });
});
