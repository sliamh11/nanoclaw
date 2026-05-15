/**
 * Host-side tool proxy for container agents.
 *
 * Containers call POST /tool/:cli-name with { args, compact?, timeout? }.
 * The proxy executes the registered host binary (never arbitrary commands),
 * injects credentials from the host environment, and returns:
 *   { exit: number, stdout: string, stderr: string }
 *
 * Exit codes follow the printing-press-adoption ADR typed exit code convention:
 *   0 = SUCCESS, 2 = USAGE_ERROR, 3 = NOT_FOUND, 4 = AUTH_ERROR, 5 = INTERNAL_ERROR
 *
 * Security:
 *   - Allowlist-only: tool name must be registered in ~/.deus/tool-registry.json
 *   - Arg sanitization: shell metacharacters rejected (no shell is involved, but
 *     defense-in-depth against binaries that parse args unsafely)
 *   - Auth: same x-deus-proxy-token gate as credential-proxy.ts
 *   - Credentials injected via process.env at spawn time, never passed to containers
 *
 * Pattern: mirrors startCredentialProxy in credential-proxy.ts.
 */

import { createServer, Server } from 'http';
import { execFile } from 'child_process';

import { DEUS_PROXY_AUTH_ENABLED } from './config.js';
import { validateGroupToken } from './group-tokens.js';
import { logger } from './logger.js';
import { loadRegistry, isAllowed, getToolConfig } from './tool-registry.js';

/** Default per-execution timeout in milliseconds. */
const DEFAULT_TOOL_TIMEOUT_MS = 30_000;

/** Regex for valid tool names — lowercase letters, digits, hyphens only. */
const TOOL_NAME_RE = /^[a-z][a-z0-9-]*$/;

/**
 * Shell metacharacters to reject in args.
 * execFile does not invoke a shell, but defense-in-depth for binaries that
 * internally parse args via a shell (e.g., scripts that call eval or system()).
 */
const SHELL_META_RE = /[;|&$`\n\r\0]/;

/** Validate a single argument string. Returns error message or null if safe. */
function validateArg(arg: string): string | null {
  if (typeof arg !== 'string') return 'all args must be strings';
  if (SHELL_META_RE.test(arg))
    return `arg contains forbidden character: ${arg}`;
  return null;
}

export function startToolProxy(
  port: number,
  host = '127.0.0.1',
): Promise<Server> {
  // Warm the registry cache on startup so the first request doesn't cold-load.
  loadRegistry();

  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        // ── Auth gate (same as credential-proxy) ──────────────────────────
        if (DEUS_PROXY_AUTH_ENABLED) {
          const token = req.headers['x-deus-proxy-token'] as string | undefined;
          const groupFolder = token ? validateGroupToken(token) : null;
          if (!groupFolder) {
            logger.warn(
              { url: req.url, hasToken: !!token },
              'Tool proxy rejected unauthenticated request',
            );
            res.writeHead(401);
            res.end('Unauthorized');
            return;
          }
        }

        // ── Route: POST /tool/:name ────────────────────────────────────────
        if (req.method !== 'POST') {
          res.writeHead(405, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Method not allowed' }));
          return;
        }

        // Parse and validate tool name from URL
        const urlMatch = (req.url ?? '').match(/^\/tool\/([^/?#]+)$/);
        if (!urlMatch) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({ error: 'Not found — use POST /tool/:name' }),
          );
          return;
        }

        const rawName = urlMatch[1];
        if (!TOOL_NAME_RE.test(rawName)) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: `Invalid tool name: ${rawName}` }));
          return;
        }

        // Allowlist check
        if (!isAllowed(rawName)) {
          logger.warn(
            { tool: rawName },
            'Tool proxy rejected unregistered tool',
          );
          res.writeHead(403, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: `Tool not allowed: ${rawName}` }));
          return;
        }

        // Parse body
        let body: { args?: unknown; compact?: unknown; timeout?: unknown };
        try {
          body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
        } catch {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Invalid JSON body' }));
          return;
        }

        // Validate args
        if (!Array.isArray(body.args)) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: '"args" must be an array' }));
          return;
        }

        const args = body.args as unknown[];
        for (const arg of args) {
          const argErr = validateArg(arg as string);
          if (argErr) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: argErr }));
            return;
          }
        }

        const safeArgs = args as string[];

        // Resolve tool config (binary path + injected env)
        const toolConfig = getToolConfig(rawName);
        if (!toolConfig) {
          // Should not happen (isAllowed passed), but guard anyway
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({
              error: 'Tool config disappeared after allowlist check',
            }),
          );
          return;
        }

        // Determine timeout: body > tool config > default
        const timeoutMs =
          typeof body.timeout === 'number' && body.timeout > 0
            ? body.timeout
            : (toolConfig.timeout ?? DEFAULT_TOOL_TIMEOUT_MS);

        // Append --compact if requested
        const execArgs = [...safeArgs];
        if (body.compact === true) {
          execArgs.push('--compact');
        }

        // Inject tool credentials from process.env (never from container env)
        const execEnv = { ...process.env, ...toolConfig.env };

        logger.debug(
          { tool: rawName, args: execArgs, timeout: timeoutMs },
          'Tool proxy executing',
        );

        execFile(
          toolConfig.binary,
          execArgs,
          { timeout: timeoutMs, env: execEnv },
          (err, stdout, stderr) => {
            const errAny = err as
              | (NodeJS.ErrnoException & { killed?: boolean; code?: unknown })
              | null;

            // Timeout
            if (errAny?.killed || errAny?.code === 'ETIMEDOUT') {
              logger.warn(
                { tool: rawName, timeout: timeoutMs },
                'Tool proxy execution timed out',
              );
              res.writeHead(504, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ error: 'Execution timed out' }));
              return;
            }

            // Binary not found or permission error (non-printing-press errors)
            if (errAny && typeof errAny.code === 'string') {
              logger.error(
                { err: errAny, tool: rawName },
                'Tool proxy spawn error',
              );
              res.writeHead(500, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ error: `Spawn error: ${errAny.code}` }));
              return;
            }

            // Normal execution — return exit code + output (even on non-zero exit).
            // The printing-press typed exit codes (2/3/4/5) are meaningful to callers
            // and must not be converted to HTTP errors.
            const exitCode = errAny?.code != null ? Number(errAny.code) : 0;
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ exit: exitCode, stdout, stderr }));
          },
        );
      });
    });

    // Port-retry loop — matches credential-proxy.ts pattern
    let retries = 0;
    const maxRetries = 10;
    const retryDelay = 2000;

    const tryListen = () => {
      server.listen(port, host, () => {
        logger.info({ port, host }, 'Tool proxy started');
        resolve(server);
      });
    };

    server.on('error', (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE' && retries < maxRetries) {
        retries++;
        logger.warn(
          { port, attempt: retries, maxRetries },
          'Tool proxy port in use, retrying...',
        );
        server.close();
        setTimeout(tryListen, retryDelay);
      } else {
        reject(err);
      }
    });

    tryListen();
  });
}
