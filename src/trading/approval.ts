/**
 * Approval flow for trading decisions.
 *
 * Formats trade proposals for WhatsApp/Telegram with:
 * - Bracket order details (entry, stop, target)
 * - Risk metrics
 * - 10-min TTL with staleness gate
 * - Inline approve/reject mechanism
 *
 * Design: bracket limit orders solve the approval-delay timing problem.
 * If the user approves in 5 minutes, the limit order catches the fill.
 * Staleness gate re-checks price on approval, aborts if moved >0.5%.
 */

import { randomUUID } from 'crypto';
import {
  TradeDecision,
  ApprovalRequest,
  BracketOrder,
  SafetyConfig,
} from './types.js';

/**
 * Format a trade decision into a human-readable approval message.
 * Compact format optimized for mobile chat clients.
 */
export function formatApprovalMessage(
  decision: TradeDecision,
  config: SafetyConfig,
): string {
  if (decision.action === 'HOLD' || !decision.bracketOrder) {
    return formatHoldMessage(decision);
  }

  const bo = decision.bracketOrder;
  const risk = decision.risk!;
  const paperTag = config.paperTradingOnly ? ' [PAPER]' : '';
  const expiresIn = Math.round(
    (new Date(decision.expiresAt).getTime() - Date.now()) / 60000,
  );

  const lines = [
    `TRADE PROPOSAL${paperTag}`,
    ``,
    `${bo.side} ${bo.symbol}`,
    `Entry: $${bo.entryPrice.toFixed(2)} (${bo.entryType})`,
    `Stop:  $${bo.stopLoss.toFixed(2)}`,
    `T1:    $${bo.takeProfit.toFixed(2)}`,
    `Qty:   ${bo.quantity} shares`,
    `R:R:   ${decision.setup?.riskRewardRatio.toFixed(1) ?? 'N/A'}`,
    ``,
    `Risk: $${risk.sizing.dollarRisk.toFixed(0)} (${risk.sizing.percentRisk.toFixed(1)}%)`,
    `Heat: ${risk.portfolioHeatPct.toFixed(1)}% portfolio`,
    `Regime: ${decision.regime} (${(decision.confidence * 100).toFixed(0)}% conf)`,
    ``,
    `${decision.reasoning}`,
    ``,
    `Expires in ${expiresIn}min | Drift gate: ${decision.stalenessGatePricePct}%`,
    ``,
    `Reply APPROVE or REJECT`,
  ];

  return lines.join('\n');
}

/**
 * Format a HOLD decision message (no trade).
 */
function formatHoldMessage(decision: TradeDecision): string {
  const lines = [
    `ANALYSIS: ${decision.symbol} - HOLD`,
    ``,
    `Regime: ${decision.regime} (${(decision.confidence * 100).toFixed(0)}% conf)`,
    ``,
    `${decision.reasoning}`,
  ];

  if (decision.risk && decision.risk.violations.length > 0) {
    lines.push('');
    lines.push('Violations:');
    for (const v of decision.risk.violations) {
      lines.push(`- ${v}`);
    }
  }

  return lines.join('\n');
}

/**
 * Create an approval request record with TTL.
 */
export function createApprovalRequest(
  decision: TradeDecision,
  channel: 'whatsapp' | 'telegram',
  ttlMin: number,
): ApprovalRequest {
  const now = new Date();
  const expiresAt = new Date(now.getTime() + ttlMin * 60 * 1000);

  return {
    requestId: randomUUID(),
    decision,
    sentAt: now.toISOString(),
    expiresAt: expiresAt.toISOString(),
    status: 'pending',
    channel,
  };
}

/**
 * Check if an approval request has expired.
 */
export function isApprovalExpired(request: ApprovalRequest): boolean {
  return new Date() >= new Date(request.expiresAt);
}

/**
 * Parse user response to an approval message.
 * Returns null if message is not an approval/rejection.
 */
export function parseApprovalResponse(
  message: string,
): 'approved' | 'rejected' | null {
  const normalized = message.trim().toLowerCase();

  // Exact matches and common variants
  const approvePatterns = ['approve', 'approved', 'yes', 'go', 'execute', 'ok'];
  const rejectPatterns = ['reject', 'rejected', 'no', 'cancel', 'abort', 'skip'];

  if (approvePatterns.includes(normalized)) return 'approved';
  if (rejectPatterns.includes(normalized)) return 'rejected';

  return null;
}

/**
 * Build a bracket order from a trade decision's setup.
 */
export function buildBracketOrder(
  decision: TradeDecision,
): BracketOrder | null {
  if (decision.action === 'HOLD' || !decision.setup || !decision.risk) {
    return null;
  }

  const setup = decision.setup;
  const shares = decision.risk.sizing.shares;
  if (shares <= 0) return null;

  return {
    side: decision.action as 'BUY' | 'SELL',
    symbol: setup.symbol,
    quantity: shares,
    entryType: 'LMT',
    entryPrice: setup.entryPrice,
    stopLoss: setup.stopLoss,
    takeProfit: setup.targets[0] ?? setup.entryPrice, // T1
    timeInForce: 'DAY', // Default DAY, override to GTD for multi-day theses
  };
}

/**
 * Format bracket order for IBKR MCP place_bracket_order call.
 * Returns the parameters object for the MCP tool.
 */
export function formatForIBKR(order: BracketOrder): {
  symbol: string;
  side: string;
  quantity: number;
  orderType: string;
  price: number;
  stopPrice: number;
  takeProfitPrice: number;
  tif: string;
} {
  return {
    symbol: order.symbol,
    side: order.side,
    quantity: order.quantity,
    orderType: order.entryType === 'LMT' ? 'LMT' : 'STP_LMT',
    price: order.entryPrice,
    stopPrice: order.stopLoss,
    takeProfitPrice: order.takeProfit,
    tif: order.timeInForce,
  };
}
