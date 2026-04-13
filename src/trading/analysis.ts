/**
 * Trading analysis engine orchestrator.
 *
 * Runs the 5-step analysis chain:
 * 1. Regime Detection (code-computed from indicators)
 * 2. Multi-Timeframe Bias (LLM-analyzed from TV data)
 * 3. Setup Identification (LLM-analyzed from chart + indicators)
 * 4. Risk Assessment (code-computed + LLM qualitative check)
 * 5. Decision (LLM synthesis)
 *
 * The engine does NOT execute trades — it produces TradeDecision objects
 * that flow to the approval module for human review.
 *
 * Data flow:
 *   TradingView MCP → indicator values → this engine → decision
 *   Decision → approval.ts → WhatsApp/Telegram → user
 *   User approves → ibkr-mcp → bracket order
 */

import { randomUUID } from 'crypto';
import { logger } from '../logger.js';
import { loadSafetyConfig } from './config.js';
import { detectRegime, regimeAllowsTrading, type RegimeInput } from './regime.js';
import { checkSafetyRails } from './safety.js';
import { buildBracketOrder } from './approval.js';
import {
  AnalysisResult,
  TradeDecision,
  RegimeSignal,
  MultiTFResult,
  SetupResult,
  RiskResult,
  PortfolioState,
  SafetyConfig,
} from './types.js';

export interface AnalysisInput {
  symbol: string;
  regimeIndicators: RegimeInput;
  portfolio: PortfolioState;
  // These will be populated by LLM analysis in the full pipeline.
  // For now, they can be provided directly for testing.
  multiTF?: MultiTFResult;
  setup?: SetupResult;
  qualitativeRisk?: {
    earningsRisk: boolean;
    macroRisk: string;
    overrideAdvice: string;
  };
  /** Override current time for testing (market hours check). */
  now?: Date;
}

/**
 * Run the full analysis chain for a symbol.
 *
 * Steps 1 and 4 are code-computed (deterministic).
 * Steps 2, 3, 5 require LLM calls (via prompt templates).
 *
 * This function handles the code-computed steps and produces
 * a decision. The caller is responsible for running LLM steps
 * (using prompts.ts templates) and feeding results back here.
 */
export function runAnalysis(input: AnalysisInput): AnalysisResult {
  const startTime = Date.now();
  const requestId = randomUUID();
  const config = loadSafetyConfig();

  // --- Step 1: Regime Detection (code-computed) ---
  const regime = detectRegime(input.regimeIndicators);
  logger.debug(
    { symbol: input.symbol, regime: regime.regime, confidence: regime.confidence },
    'trading: regime detected',
  );

  // --- Regime gate ---
  const regimeCheck = regimeAllowsTrading(regime);
  if (!regimeCheck.allowed) {
    return buildHoldResult(requestId, input.symbol, regime, regimeCheck.reason, startTime);
  }

  // --- Step 2: Multi-TF Bias (requires LLM or pre-computed) ---
  const multiTF = input.multiTF ?? buildDefaultMultiTF();

  // --- Step 3: Setup Identification (requires LLM or pre-computed) ---
  const setup = input.setup ?? null;
  if (!setup || setup.type === ('NONE' as string)) {
    return buildHoldResult(requestId, input.symbol, regime, 'No valid setup found', startTime);
  }

  // --- Step 4: Risk Assessment (code-computed) ---
  const risk = checkSafetyRails(config, input.portfolio, setup, regime, input.now);

  // Check for earnings (from qualitative risk if provided)
  if (input.qualitativeRisk?.earningsRisk) {
    risk.earningsWithin48h = true;
    risk.violations.push('earnings: within 48h blackout');
  }

  // Check qualitative override
  if (input.qualitativeRisk?.overrideAdvice === 'abort') {
    risk.violations.push('qualitative: risk manager recommends abort');
  }

  // --- Step 5: Decision (deterministic from chain results) ---
  const decision = buildDecision(input.symbol, regime, multiTF, setup, risk, config);

  const durationMs = Date.now() - startTime;
  logger.info(
    {
      requestId,
      symbol: input.symbol,
      action: decision.action,
      confidence: decision.confidence,
      violations: risk.violations.length,
      durationMs,
    },
    'trading: analysis complete',
  );

  return {
    requestId,
    timestamp: new Date().toISOString(),
    symbol: input.symbol,
    regime,
    multiTF,
    setup,
    risk,
    decision,
    durationMs,
  };
}

/**
 * Build a HOLD decision result (for early exits).
 */
function buildHoldResult(
  requestId: string,
  symbol: string,
  regime: RegimeSignal,
  reason: string,
  startTime: number,
): AnalysisResult {
  const decision: TradeDecision = {
    action: 'HOLD',
    confidence: 0,
    symbol,
    regime: regime.regime,
    setup: null,
    risk: null,
    reasoning: reason,
    bracketOrder: null,
    expiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    stalenessGatePricePct: 0.5,
  };

  return {
    requestId,
    timestamp: new Date().toISOString(),
    symbol,
    regime,
    multiTF: buildDefaultMultiTF(),
    setup: null,
    risk: null,
    decision,
    durationMs: Date.now() - startTime,
  };
}

/**
 * Build trade decision from chain results.
 * Hard rules enforced here — LLM cannot override safety violations.
 */
function buildDecision(
  symbol: string,
  regime: RegimeSignal,
  multiTF: MultiTFResult,
  setup: SetupResult,
  risk: RiskResult,
  config: SafetyConfig,
): TradeDecision {
  // Hard rule: any violation = HOLD
  if (risk.violations.length > 0) {
    const nonPaperViolations = risk.violations.filter((v) => !v.startsWith('paper-mode'));
    const reasoning = nonPaperViolations.length > 0
      ? `Blocked: ${nonPaperViolations[0]}`
      : `Paper mode active. Would ${multiTF.overallBias === 'bullish' ? 'BUY' : 'SELL'}: ${setup.type} at $${setup.entryPrice.toFixed(2)}`;

    // Paper mode is a soft-block — still show the proposal
    if (nonPaperViolations.length === 0) {
      const bracketOrder = buildBracketOrder({
        action: multiTF.overallBias === 'bullish' ? 'BUY' : 'SELL',
        confidence: calculateConfidence(regime, multiTF, setup, risk),
        symbol,
        regime: regime.regime,
        setup,
        risk,
        reasoning,
        bracketOrder: null,
        expiresAt: new Date(Date.now() + config.approvalTtlMin * 60 * 1000).toISOString(),
        stalenessGatePricePct: config.stalenessGatePct,
      });

      return {
        action: multiTF.overallBias === 'bullish' ? 'BUY' : 'SELL',
        confidence: calculateConfidence(regime, multiTF, setup, risk),
        symbol,
        regime: regime.regime,
        setup,
        risk,
        reasoning,
        bracketOrder,
        expiresAt: new Date(Date.now() + config.approvalTtlMin * 60 * 1000).toISOString(),
        stalenessGatePricePct: config.stalenessGatePct,
      };
    }

    return {
      action: 'HOLD',
      confidence: 0,
      symbol,
      regime: regime.regime,
      setup,
      risk,
      reasoning,
      bracketOrder: null,
      expiresAt: new Date(Date.now() + config.approvalTtlMin * 60 * 1000).toISOString(),
      stalenessGatePricePct: config.stalenessGatePct,
    };
  }

  // No violations — produce trade signal
  const action = multiTF.overallBias === 'bullish' ? 'BUY' : 'SELL';
  const confidence = calculateConfidence(regime, multiTF, setup, risk);

  // Low confidence = HOLD
  if (confidence < 0.4) {
    return {
      action: 'HOLD',
      confidence,
      symbol,
      regime: regime.regime,
      setup,
      risk,
      reasoning: `Marginal confidence (${(confidence * 100).toFixed(0)}%). Signals mixed.`,
      bracketOrder: null,
      expiresAt: new Date(Date.now() + config.approvalTtlMin * 60 * 1000).toISOString(),
      stalenessGatePricePct: config.stalenessGatePct,
    };
  }

  const decision: TradeDecision = {
    action,
    confidence,
    symbol,
    regime: regime.regime,
    setup,
    risk,
    reasoning: `${setup.type} setup, ${regime.regime} regime, ${multiTF.overallBias} bias. ${risk.sizing.shares}sh @ $${setup.entryPrice.toFixed(2)}.`,
    bracketOrder: null,
    expiresAt: new Date(Date.now() + config.approvalTtlMin * 60 * 1000).toISOString(),
    stalenessGatePricePct: config.stalenessGatePct,
  };

  decision.bracketOrder = buildBracketOrder(decision);
  return decision;
}

/**
 * Calculate composite confidence from all chain signals.
 *
 * Weighted average:
 * - Regime confidence: 25%
 * - MTF alignment: 25%
 * - Confluence score (normalized): 30%
 * - Risk profile (inverse of heat): 20%
 */
function calculateConfidence(
  regime: RegimeSignal,
  multiTF: MultiTFResult,
  setup: SetupResult,
  risk: RiskResult,
): number {
  const regimeConf = regime.confidence * 0.25;
  const mtfConf = multiTF.alignment * 0.25;

  // Normalize confluence: 3.0 = minimum (0.5), 5.0+ = max (1.0)
  const confluenceNorm = Math.min(1, Math.max(0.5, (setup.confluenceScore - 3) / 2 + 0.5));
  const confluenceConf = confluenceNorm * 0.30;

  // Risk profile: lower heat = higher confidence
  const riskConf = Math.max(0.3, 1 - risk.portfolioHeatPct / 10) * 0.20;

  const total = regimeConf + mtfConf + confluenceConf + riskConf;
  return Math.round(total * 100) / 100;
}

/**
 * Default multi-TF result for when LLM analysis is not yet available.
 */
function buildDefaultMultiTF(): MultiTFResult {
  return {
    readings: [],
    overallBias: 'neutral',
    alignment: 0,
    conflictingTFs: [],
  };
}
