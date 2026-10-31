import net from 'node:net';
import { resolve } from 'node:path';
import process from 'node:process';
import { clearTimeout, setTimeout } from 'node:timers';
import { pathToFileURL } from 'node:url';

export const E2E_API_IDENTITY_URL = 'http://127.0.0.1:8000/__e2e__/identity';

export const EXPECTED_FIXTURE_IDENTITY = Object.freeze({
  fixture_id: 'ged-b3-e2e-deterministic-v1',
  reader: 'DeterministicNesoReader',
  neso_network_policy: 'blocked',
  neso_transport_attempts: 0,
});

const identityError = () => new Error(
  'Refusing browser navigation: deterministic E2E fixture identity did not match; no application or scheduling requests were sent.',
);

export function assertFixtureIdentity(payload) {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw identityError();
  }

  for (const [key, expectedValue] of Object.entries(EXPECTED_FIXTURE_IDENTITY)) {
    if (payload[key] !== expectedValue) throw identityError();
  }

  return payload;
}

export async function verifyE2EFixtureIdentity(fetchImpl = globalThis.fetch, timeoutMs = 5_000) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error('E2E fixture identity timeout must be a positive finite number of milliseconds.');
  }

  const controller = new globalThis.AbortController();
  let timeout;
  const verification = (async () => {
    try {
      const response = await fetchImpl(E2E_API_IDENTITY_URL, {
        method: 'GET',
        redirect: 'error',
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(
          `E2E fixture identity check returned HTTP ${response.status}; refusing browser navigation and application API calls.`,
        );
      }

      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        throw new Error(
          'E2E fixture identity response was not valid JSON; refusing browser navigation and application API calls.',
          { cause: error },
        );
      }
      return assertFixtureIdentity(payload);
    } catch (error) {
      if (error instanceof Error && (
        error.message.startsWith('E2E fixture identity check')
        || error.message.startsWith('E2E fixture identity response')
        || error.message.startsWith('Refusing browser navigation')
      )) {
        throw error;
      }
      throw new Error(
        `E2E fixture identity could not be verified at ${E2E_API_IDENTITY_URL}; refusing browser navigation and application API calls.`,
        { cause: error },
      );
    }
  })();
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      controller.abort();
      reject(new Error(
        `E2E fixture identity check timed out after ${timeoutMs}ms at ${E2E_API_IDENTITY_URL}; refusing browser navigation and application API calls.`,
      ));
    }, timeoutMs);
  });

  try {
    return await Promise.race([verification, deadline]);
  } finally {
    clearTimeout(timeout);
  }
}

export async function assertPortAvailable(port, host = '127.0.0.1') {
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`Invalid E2E port ${port}; expected an integer from 1 to 65535.`);
  }

  const probe = net.createServer();
  await new Promise((resolvePromise, rejectPromise) => {
    const onError = (error) => {
      if (error.code === 'EADDRINUSE') {
        rejectPromise(new Error(
          `E2E startup refused: ${host}:${port} is already occupied; Playwright will not reuse or terminate existing services.`,
          { cause: error },
        ));
        return;
      }
      rejectPromise(new Error(`E2E port preflight failed for ${host}:${port}: ${error.message}`, { cause: error }));
    };

    probe.once('error', onError);
    probe.listen({ host, port, exclusive: true }, () => {
      probe.removeListener('error', onError);
      probe.close((error) => {
        if (error) rejectPromise(error);
        else resolvePromise();
      });
    });
  });
}

async function main() {
  const port = Number(process.argv[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    process.stderr.write('Usage: node e2e-server-guard.mjs <loopback-port>\n');
    process.exitCode = 2;
    return;
  }

  try {
    await assertPortAvailable(port);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  await main();
}
