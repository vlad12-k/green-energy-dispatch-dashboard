import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import process from 'node:process';
import { setTimeout } from 'node:timers';
import { fileURLToPath, URL } from 'node:url';
import test from 'node:test';
import { assertPortAvailable } from './e2e-server-guard.mjs';

const guardScript = fileURLToPath(new URL('./e2e-server-guard.mjs', import.meta.url));
const frontendRoot = fileURLToPath(new URL('..', import.meta.url));
const playwrightCli = resolve(frontendRoot, 'node_modules/playwright/cli.js');

function listen(server, port = 0) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      server.removeListener('error', reject);
      const address = server.address();
      if (!address || typeof address === 'string') {
        reject(new Error('Expected the local test listener to have a TCP address'));
        return;
      }
      resolve(address.port);
    });
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

function isPortFree(port) {
  return assertPortAvailable(port).then(() => true).catch((error) => {
    if (error instanceof Error && error.message.includes('is already occupied')) return false;
    throw error;
  });
}

async function verifyPlaywrightRejectsOccupiedPort(port, t) {
  if (!(await isPortFree(8000)) || !(await isPortFree(5173))) {
    t.skip('A required fixed port is already occupied; leaving the existing owner untouched.');
    return;
  }

  const requests = [];
  const server = createServer((request, response) => {
    requests.push(request.url);
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{"status":"ok"}');
  });
  try {
    await listen(server, port);
  } catch (error) {
    if (error.code === 'EADDRINUSE') {
      t.skip(`Port ${port} became occupied; leaving the existing owner untouched.`);
      return;
    }
    throw error;
  }

  const outputDirectory = mkdtempSync(join(tmpdir(), 'ged-e2e-port-guard-'));
  try {
    const result = spawnSync(process.execPath, [
      playwrightCli,
      'test',
      '--config',
      'playwright.config.ts',
      '--grep',
      'E2E API exposes its deterministic fixture identity before browser navigation',
      '--workers=1',
      '--output',
      outputDirectory,
    ], {
      cwd: frontendRoot,
      encoding: 'utf8',
      timeout: 45_000,
      env: { ...process.env, CI: '' },
    });
    const output = `${result.stdout ?? ''}${result.stderr ?? ''}`;
    assert.equal(result.error, undefined, output);
    assert.notEqual(result.status, 0, output);
    assert.ok(
      output.includes(`E2E startup refused: 127.0.0.1:${port} is already occupied`),
      output,
    );
    assert.deepEqual(requests, [], 'the occupied listener must not be used for readiness or identity checks');
    assert.equal(server.listening, true, 'Playwright must not terminate the occupied listener');

    const response = await globalThis.fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(response.status, 200, 'the test-owned listener should remain responsive');
    assert.equal(await isPortFree(port === 8000 ? 5173 : 8000), true, 'Playwright must clean up only its own server');
  } finally {
    await close(server);
    rmSync(outputDirectory, { recursive: true, force: true });
  }
}

test('port preflight refuses an occupied listener without contacting or stopping it', async (t) => {
  let requests = 0;
  const server = createServer((_request, response) => {
    requests += 1;
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{"status":"ok"}');
  });
  const port = await listen(server);
  t.after(() => close(server));

  const result = spawnSync(process.execPath, [guardScript, String(port)], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(`${result.stdout}${result.stderr}`, /E2E startup refused: 127\.0\.0\.1:\d+ is already occupied/);
  assert.equal(requests, 0);
  assert.equal(server.listening, true);
});

test('fixture identity validation rejects missing or unexpected servers', async () => {
  const { assertFixtureIdentity } = await import('./e2e-server-guard.mjs');
  assert.throws(
    () => assertFixtureIdentity({ fixture_id: 'untrusted-local-server' }),
    /Refusing browser navigation: deterministic E2E fixture identity did not match/,
  );
});

test('fixture identity validation accepts the deterministic test API marker', async () => {
  const { assertFixtureIdentity, EXPECTED_FIXTURE_IDENTITY } = await import('./e2e-server-guard.mjs');
  assert.deepEqual(assertFixtureIdentity({ ...EXPECTED_FIXTURE_IDENTITY }), EXPECTED_FIXTURE_IDENTITY);
});

test('fixture identity verification aborts when its response body stalls', async () => {
  const { verifyE2EFixtureIdentity } = await import('./e2e-server-guard.mjs');
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: () => new Promise(() => {}),
  });
  const verification = verifyE2EFixtureIdentity(fetchImpl, 10);
  const watchdog = new Promise((_, reject) => {
    setTimeout(() => reject(new Error('fixture identity guard remained pending')), 100);
  });
  await assert.rejects(
    Promise.race([verification, watchdog]),
    /E2E fixture identity check timed out/,
  );
});

test('Playwright never reuses servers and preflights both fixed loopback ports', async () => {
  const config = await readFile(new URL('../playwright.config.ts', import.meta.url), 'utf8');
  assert.equal((config.match(/reuseExistingServer: false/g) ?? []).length, 2);
  assert.match(config, /e2e-server-guard\.mjs 8000/);
  assert.match(config, /e2e-server-guard\.mjs 5173/);
});

test('Playwright aborts on an occupied FastAPI port without touching its owner', async (t) => {
  await verifyPlaywrightRejectsOccupiedPort(8000, t);
});

test('Playwright aborts on an occupied Vite port without touching its owner', async (t) => {
  await verifyPlaywrightRejectsOccupiedPort(5173, t);
});
