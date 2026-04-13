/**
 * Trading analysis module — public API.
 *
 * Private to the Deus repo. Not part of ibkr-mcp (open source).
 * Contains: expert prompt chain, regime detection, safety rails,
 * approval flow, and analysis orchestration.
 */

// Config
export { TRADING_ENABLED, loadSafetyConfig } from './config.js';

// Types
export type {
  MarketRegime,
  RegimeSignal,
  TimeframeBias,
  TimeframeReading,
  MultiTFResult,
  SetupType,
  ConfluenceFactor,
  SetupResult,
  PositionSizing,
  RiskResult,
  TradeAction,
  TradeDecision,
  BracketOrder,
  PortfolioState,
  OpenPosition,
  AnalysisResult,
  ApprovalStatus,
  ApprovalRequest,
  SafetyConfig,
} from './types.js';

// Regime detection
export { detectRegime, regimeAllowsTrading, getRegimeParams } from './regime.js';
export type { RegimeInput } from './regime.js';

// Safety rails
export {
  checkSafetyRails,
  calculatePositionSize,
  estimateCorrelation,
  calculatePortfolioHeat,
  checkMarketHoursBuffer,
  checkStalenessGate,
} from './safety.js';

// Prompt chain
export {
  buildRegimePrompt,
  buildMultiTFPrompt,
  buildSetupPrompt,
  buildRiskPrompt,
  buildDecisionPrompt,
  getPromptChainSteps,
} from './prompts.js';

// Approval flow
export {
  formatApprovalMessage,
  createApprovalRequest,
  isApprovalExpired,
  parseApprovalResponse,
  buildBracketOrder,
  formatForIBKR,
} from './approval.js';

// Analysis engine
export { runAnalysis } from './analysis.js';
export type { AnalysisInput } from './analysis.js';
