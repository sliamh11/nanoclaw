/**
 * Trading analysis system types.
 *
 * Structured JSON output schemas for each analysis chain step.
 * All types are designed for token-efficient serialization.
 */

// --- Regime Detection ---

export type MarketRegime = 'trending-up' | 'trending-down' | 'ranging' | 'volatile';

export interface RegimeSignal {
  regime: MarketRegime;
  confidence: number; // 0-1
  adx: number;
  vix: number;
  hmmState?: number; // Hidden Markov Model state index
  timestamp: string; // ISO 8601
}

// --- Multi-Timeframe Bias ---

export type TimeframeBias = 'bullish' | 'bearish' | 'neutral';

export interface TimeframeReading {
  tf: string; // "D" | "4H" | "1H" | "15m"
  bias: TimeframeBias;
  keyLevel: number; // Nearest S/R level
  emaAlignment: boolean; // 9/21/50 alignment
  volume: 'high' | 'normal' | 'low';
}

export interface MultiTFResult {
  readings: TimeframeReading[];
  overallBias: TimeframeBias;
  alignment: number; // 0-1, how aligned timeframes are
  conflictingTFs: string[]; // TFs that disagree with majority
}

// --- Setup Identification ---

export type SetupType =
  | 'breakout'
  | 'pullback'
  | 'reversal'
  | 'range-bounce'
  | 'momentum'
  | 'gap-fill';

export interface ConfluenceFactor {
  name: string; // e.g. "ema-crossover", "volume-spike", "sr-level"
  weight: number; // 0-1
  description: string;
}

export interface SetupResult {
  type: SetupType;
  symbol: string;
  confluenceFactors: ConfluenceFactor[];
  confluenceScore: number; // sum of weights, min 3.0 required
  entryPrice: number;
  stopLoss: number;
  targets: number[]; // T1, T2, T3
  riskRewardRatio: number;
  pattern?: string; // e.g. "bull-flag", "double-bottom"
}

// --- Risk Assessment ---

export interface PositionSizing {
  shares: number;
  dollarRisk: number;
  percentRisk: number; // of portfolio
  kellyFraction: number; // Raw Kelly
  adjustedFraction: number; // After VIX adjustment
}

export interface RiskResult {
  sizing: PositionSizing;
  atrStop: number; // ATR-based stop distance
  correlationRisk: number; // 0-1, correlation with existing positions
  portfolioHeatPct: number; // Current total portfolio risk %
  dailyDrawdownPct: number; // Current day's realized+unrealized loss %
  earningsWithin48h: boolean;
  violations: string[]; // Safety rail violations, empty = pass
}

// --- Decision ---

export type TradeAction = 'BUY' | 'SELL' | 'HOLD';

export interface TradeDecision {
  action: TradeAction;
  confidence: number; // 0-1
  symbol: string;
  regime: MarketRegime;
  setup: SetupResult | null;
  risk: RiskResult | null;
  reasoning: string; // 1-2 sentence explanation
  bracketOrder: BracketOrder | null;
  expiresAt: string; // ISO 8601, 10 min from generation
  stalenessGatePricePct: number; // Max price drift before abort (default 0.5%)
}

export interface BracketOrder {
  side: 'BUY' | 'SELL';
  symbol: string;
  quantity: number;
  entryType: 'LMT' | 'STP_LMT';
  entryPrice: number;
  stopLoss: number;
  takeProfit: number; // T1 target
  timeInForce: 'DAY' | 'GTC' | 'GTD';
  gtdExpiry?: string; // ISO date for GTD orders
}

// --- Portfolio State (input to risk assessment) ---

export interface PortfolioState {
  accountValue: number;
  cashAvailable: number;
  openPositions: OpenPosition[];
  dailyPnl: number;
  dailyPnlPct: number;
}

export interface OpenPosition {
  symbol: string;
  shares: number;
  avgCost: number;
  currentPrice: number;
  unrealizedPnl: number;
  sector?: string;
}

// --- Analysis Chain Result ---

export interface AnalysisResult {
  requestId: string;
  timestamp: string;
  symbol: string;
  regime: RegimeSignal;
  multiTF: MultiTFResult;
  setup: SetupResult | null;
  risk: RiskResult | null;
  decision: TradeDecision;
  durationMs: number;
}

// --- Approval Flow ---

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired';

export interface ApprovalRequest {
  requestId: string;
  decision: TradeDecision;
  sentAt: string; // ISO 8601
  expiresAt: string; // ISO 8601
  status: ApprovalStatus;
  respondedAt?: string;
  channel: 'whatsapp' | 'telegram';
}

// --- Safety Config ---

export interface SafetyConfig {
  maxRiskPerTradePct: number; // Default: 2
  dailyDrawdownLimitPct: number; // Default: 3
  maxOpenPositions: number; // Default: 5
  maxCorrelation: number; // Default: 0.7
  earningsBlackoutHours: number; // Default: 48
  marketOpenBufferMin: number; // Default: 15
  marketCloseBufferMin: number; // Default: 15
  approvalTtlMin: number; // Default: 10
  stalenessGatePct: number; // Default: 0.5
  paperTradingOnly: boolean; // Default: true (safe default)
  allowedExchanges: string[]; // Default: ["NYSE", "NASDAQ"]
}
