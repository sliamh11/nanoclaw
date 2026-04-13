/**
 * Trading analysis prompt chain templates.
 *
 * 5-step analysis chain, each step a separate compact prompt.
 * Designed for token efficiency (caveman-style, no fluff).
 * Structured JSON output for every step.
 *
 * Chain order:
 * 1. Regime Detection → trending/ranging/volatile classification
 * 2. Multi-Timeframe Bias → D/4H/1H/15m alignment
 * 3. Setup Identification → pattern + confluence scoring
 * 4. Risk Assessment → ATR sizing, portfolio correlation
 * 5. Decision → BUY/SELL/HOLD + bracket order
 *
 * Academic backing:
 * - Regime-gating non-optional (FINSABER, KDD 2026)
 * - Signal diversity > model scale (TradingAgents, arXiv:2412.20138)
 * - Confidence signaling for override quality (arXiv:2603.22567)
 * - Hybrid Kelly-VIX sizing (arXiv:2508.16598)
 * - Multimodal input beats text-only (FinAgent, arXiv:2402.18485)
 */

import {
  RegimeSignal,
  MultiTFResult,
  SetupResult,
  RiskResult,
  SafetyConfig,
  PortfolioState,
} from './types.js';

/**
 * Step 1: Regime Detection Prompt
 *
 * Input: ADX, VIX, SMA relationships, ATR
 * Output: RegimeSignal JSON
 */
export function buildRegimePrompt(indicators: {
  symbol: string;
  adx: number;
  vix: number;
  sma50: number;
  sma200: number;
  currentPrice: number;
  atr14: number;
  avgVolume20d: number;
  currentVolume: number;
}): string {
  const i = indicators;
  const priceVsSma50 = ((i.currentPrice - i.sma50) / i.sma50) * 100;
  const priceVsSma200 = ((i.currentPrice - i.sma200) / i.sma200) * 100;
  const atrPct = (i.atr14 / i.currentPrice) * 100;
  const volRatio = i.currentVolume / i.avgVolume20d;

  return `You are a market regime classifier. Classify ${i.symbol} into exactly one regime.

DATA:
- ADX(14): ${i.adx.toFixed(1)}
- VIX: ${i.vix.toFixed(1)}
- Price vs SMA50: ${priceVsSma50 > 0 ? '+' : ''}${priceVsSma50.toFixed(2)}%
- Price vs SMA200: ${priceVsSma200 > 0 ? '+' : ''}${priceVsSma200.toFixed(2)}%
- ATR(14): ${i.atr14.toFixed(2)} (${atrPct.toFixed(2)}% of price)
- Volume ratio (vs 20d avg): ${volRatio.toFixed(2)}x

RULES:
- VIX>30 = volatile (override other signals)
- ADX>25 + price>SMA50 = trending-up
- ADX>25 + price<SMA50 = trending-down
- ADX<20 + VIX<20 = ranging
- Edge cases: weight all signals, pick strongest
- Confidence: 0.0-1.0, binary gate at 0.5

RESPOND JSON ONLY:
{"regime":"trending-up|trending-down|ranging|volatile","confidence":0.XX,"reasoning":"one sentence"}`;
}

/**
 * Step 2: Multi-Timeframe Bias Prompt
 *
 * Input: D/4H/1H/15m indicator values
 * Output: MultiTFResult JSON
 */
export function buildMultiTFPrompt(data: {
  symbol: string;
  regime: RegimeSignal;
  timeframes: Array<{
    tf: string;
    ema9: number;
    ema21: number;
    ema50: number;
    rsi: number;
    macdHistogram: number;
    currentPrice: number;
    volume: string; // "high" | "normal" | "low"
    nearestSR: number;
  }>;
}): string {
  const tfLines = data.timeframes.map((tf) => {
    const emaAligned = tf.ema9 > tf.ema21 && tf.ema21 > tf.ema50;
    const emaReverse = tf.ema9 < tf.ema21 && tf.ema21 < tf.ema50;
    return `${tf.tf}: EMA9=${tf.ema9.toFixed(2)} EMA21=${tf.ema21.toFixed(2)} EMA50=${tf.ema50.toFixed(2)} ` +
      `aligned=${emaAligned ? 'bull' : emaReverse ? 'bear' : 'mixed'} ` +
      `RSI=${tf.rsi.toFixed(1)} MACD-H=${tf.macdHistogram > 0 ? '+' : ''}${tf.macdHistogram.toFixed(3)} ` +
      `vol=${tf.volume} SR=${tf.nearestSR.toFixed(2)}`;
  });

  return `You are a multi-timeframe analyst. Determine bias for ${data.symbol}.
Regime: ${data.regime.regime} (conf=${data.regime.confidence})

TIMEFRAMES (top-down, Daily bias dominates):
${tfLines.join('\n')}

RULES:
- Daily sets the thesis. 4H confirms. 1H times entry. 15m fine-tunes.
- EMA stack (9>21>50) = bullish alignment
- RSI>60 = bullish momentum, RSI<40 = bearish
- MACD histogram sign + direction = momentum confirmation
- Count aligned TFs for alignment score (0-1)

RESPOND JSON ONLY:
{"overallBias":"bullish|bearish|neutral","alignment":0.XX,"conflictingTFs":["tf1"],"readings":[{"tf":"D","bias":"bullish|bearish|neutral","keyLevel":X.XX,"emaAlignment":true|false,"volume":"high|normal|low"}]}`;
}

/**
 * Step 3: Setup Identification Prompt
 *
 * Input: Regime, MTF bias, chart data, indicator values
 * Output: SetupResult JSON
 */
export function buildSetupPrompt(data: {
  symbol: string;
  currentPrice: number;
  regime: RegimeSignal;
  mtfBias: MultiTFResult;
  atr14: number;
  recentCandles: string; // Compact OHLCV summary
  srLevels: number[];
  indicators: string; // Compact indicator summary
}): string {
  const preferredByRegime: Record<string, string> = {
    'trending-up': 'breakout, pullback, momentum',
    'trending-down': 'pullback, reversal',
    'ranging': 'range-bounce, reversal',
    'volatile': 'pullback only (high-confidence)',
  };

  return `You are a setup analyst. Find tradeable setup for ${data.symbol} or declare NONE.

CONTEXT:
- Price: $${data.currentPrice.toFixed(2)}
- Regime: ${data.regime.regime} (conf=${data.regime.confidence})
- MTF bias: ${data.mtfBias.overallBias} (alignment=${data.mtfBias.alignment})
- ATR(14): $${data.atr14.toFixed(2)}
- Preferred setups (regime-gated): ${preferredByRegime[data.regime.regime] ?? 'none'}

S/R LEVELS: ${data.srLevels.map((l) => l.toFixed(2)).join(', ')}

CANDLES (recent):
${data.recentCandles}

INDICATORS:
${data.indicators}

RULES:
- Minimum 3 confluence factors (weight>=1.0 each)
- R:R >= 2.0 (stop to T1)
- Stop placement: below nearest structure + 0.5*ATR buffer
- T1 at 1:2 R:R, T2 at 1:3, T3 at next major resistance
- If no valid setup with >=3 confluence, respond with type="NONE"
- MTF conflict (alignment<0.5) = skip unless regime is strong trend

RESPOND JSON ONLY:
{"type":"breakout|pullback|reversal|range-bounce|momentum|gap-fill|NONE","confluenceFactors":[{"name":"factor-id","weight":1.0,"description":"one line"}],"confluenceScore":X.X,"entryPrice":X.XX,"stopLoss":X.XX,"targets":[T1,T2,T3],"riskRewardRatio":X.XX,"pattern":"pattern-name|null"}`;
}

/**
 * Step 4: Risk Assessment Prompt
 *
 * Input: Setup, portfolio state, safety config
 * Output: Structured risk summary (computed, not LLM-generated)
 *
 * NOTE: Risk assessment is primarily code-computed (safety.ts),
 * not LLM-generated. This prompt is for the LLM to provide
 * qualitative risk factors the code cannot detect (news, events, sector risk).
 */
export function buildRiskPrompt(data: {
  symbol: string;
  setup: SetupResult;
  portfolio: PortfolioState;
  safetyConfig: SafetyConfig;
  codeRiskResult: RiskResult;
}): string {
  const positionList = data.portfolio.openPositions
    .map((p) => `${p.symbol}: ${p.shares}sh @${p.avgCost.toFixed(2)} (${p.unrealizedPnl >= 0 ? '+' : ''}$${p.unrealizedPnl.toFixed(2)})`)
    .join(', ') || 'none';

  return `You are a risk manager. Review this trade for qualitative risks the code cannot detect.

PROPOSED TRADE:
- ${data.setup.symbol}: ${data.setup.type} at $${data.setup.entryPrice.toFixed(2)}
- Stop: $${data.setup.stopLoss.toFixed(2)}, T1: $${data.setup.targets[0]?.toFixed(2) ?? 'N/A'}
- R:R: ${data.setup.riskRewardRatio.toFixed(2)}
- Shares: ${data.codeRiskResult.sizing.shares}
- Dollar risk: $${data.codeRiskResult.sizing.dollarRisk.toFixed(2)} (${data.codeRiskResult.sizing.percentRisk.toFixed(2)}%)

PORTFOLIO:
- Account: $${data.portfolio.accountValue.toFixed(2)}
- Daily P&L: ${data.portfolio.dailyPnlPct >= 0 ? '+' : ''}${data.portfolio.dailyPnlPct.toFixed(2)}%
- Open positions: ${positionList}
- Portfolio heat: ${data.codeRiskResult.portfolioHeatPct.toFixed(2)}%

CODE VIOLATIONS: ${data.codeRiskResult.violations.length > 0 ? data.codeRiskResult.violations.join('; ') : 'NONE'}

CHECK FOR:
- Upcoming earnings within 48h
- Major macro events (FOMC, CPI, NFP)
- Sector-specific news that could gap the stock
- Unusual options activity suggesting informed flow
- Any reason to override the code's pass/fail

RESPOND JSON ONLY:
{"earningsRisk":true|false,"macroRisk":"none|low|medium|high","sectorRisk":"one line or null","overrideAdvice":"proceed|reduce-size|abort","reasoning":"one sentence"}`;
}

/**
 * Step 5: Decision Prompt
 *
 * Input: All prior chain outputs
 * Output: Final TradeDecision JSON
 *
 * This is the synthesis step. It does NOT override safety violations —
 * those are hard blocks. It synthesizes the qualitative and quantitative
 * signals into a final recommendation.
 */
export function buildDecisionPrompt(data: {
  symbol: string;
  regime: RegimeSignal;
  mtfBias: MultiTFResult;
  setup: SetupResult | null;
  risk: RiskResult | null;
  qualitativeRisk: { earningsRisk: boolean; macroRisk: string; overrideAdvice: string };
  currentPrice: number;
  safetyConfig: SafetyConfig;
}): string {
  const hasViolations = (data.risk?.violations.length ?? 0) > 0;
  const noSetup = !data.setup || data.setup.type === ('NONE' as string);

  return `You are a trading decision engine. Produce final BUY/SELL/HOLD for ${data.symbol}.

CHAIN RESULTS:
- Regime: ${data.regime.regime} (conf=${data.regime.confidence})
- MTF bias: ${data.mtfBias.overallBias} (alignment=${data.mtfBias.alignment})
- Setup: ${noSetup ? 'NONE FOUND' : `${data.setup!.type} (confluence=${data.setup!.confluenceScore})`}
- Safety violations: ${hasViolations ? data.risk!.violations.join('; ') : 'NONE'}
- Qualitative: macro=${data.qualitativeRisk.macroRisk}, earnings=${data.qualitativeRisk.earningsRisk}, advice=${data.qualitativeRisk.overrideAdvice}
- Current price: $${data.currentPrice.toFixed(2)}

HARD RULES (NEVER OVERRIDE):
- Safety violations present → HOLD (not negotiable)
- No valid setup found → HOLD
- Regime confidence < 0.5 → HOLD
- Qualitative override = "abort" → HOLD
- Paper mode = true → tag as PAPER in reasoning

CONFIDENCE CALIBRATION:
- 0.8+ = strong alignment across all 5 chain steps
- 0.6-0.8 = good setup with minor concerns
- 0.4-0.6 = marginal, only if forced
- <0.4 = should be HOLD

RESPOND JSON ONLY:
{"action":"BUY|SELL|HOLD","confidence":0.XX,"reasoning":"1-2 sentences explaining the decision"}`;
}

/**
 * Build complete analysis chain context for a single symbol.
 * Returns array of prompt strings to be executed sequentially.
 * Each step feeds its output to the next.
 */
export function getPromptChainSteps(): string[] {
  return [
    'regime-detection',
    'multi-tf-bias',
    'setup-identification',
    'risk-assessment',
    'decision',
  ];
}
