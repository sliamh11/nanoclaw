import { DEFAULT_AGENT_RUNTIME } from '../config.js';
import { VALID_EFFORT_LEVELS } from '../types.js';
import type {
  AgentEffortLevel,
  RegisteredGroup,
  ScheduledTask,
} from '../types.js';
import type { AgentRuntimeId } from './types.js';

export function resolveAgentRuntime(
  group: RegisteredGroup,
  task?: ScheduledTask,
): AgentRuntimeId {
  if (task?.agent_backend) return task.agent_backend;
  if (group.containerConfig?.agentBackend) {
    return group.containerConfig.agentBackend;
  }
  return DEFAULT_AGENT_RUNTIME;
}

const DEFAULT_AGENT_EFFORT: AgentEffortLevel = 'low';

export function resolveAgentEffort(
  group: RegisteredGroup,
  task?: ScheduledTask,
): AgentEffortLevel {
  if (task?.agent_effort) return task.agent_effort;
  if (group.containerConfig?.agentEffort) {
    return group.containerConfig.agentEffort;
  }
  const envEffort = process.env.DEUS_AGENT_EFFORT?.toLowerCase();
  if (
    envEffort &&
    (VALID_EFFORT_LEVELS as readonly string[]).includes(envEffort)
  ) {
    return envEffort as AgentEffortLevel;
  }
  return DEFAULT_AGENT_EFFORT;
}
