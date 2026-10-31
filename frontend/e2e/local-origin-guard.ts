import type { Page } from '@playwright/test';

const EXPECTED_FRONTEND_ORIGIN = 'http://127.0.0.1:5173';
const blockedOriginsByPage = new WeakMap<Page, string[]>();

export async function installE2EOriginGuard(page: Page): Promise<void> {
  const blockedOrigins: string[] = [];
  blockedOriginsByPage.set(page, blockedOrigins);
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== EXPECTED_FRONTEND_ORIGIN) {
      blockedOrigins.push(`${route.request().method()} ${url.origin}${url.pathname}`);
      await route.abort('blockedbyclient');
      return;
    }
    await route.continue();
  });
}

export function assertNoE2EExternalRequests(page: Page): void {
  const blockedOrigins = blockedOriginsByPage.get(page) ?? [];
  blockedOriginsByPage.delete(page);
  if (blockedOrigins.length > 0) {
    throw new Error(`Blocked non-local browser requests during E2E: ${blockedOrigins.join(', ')}`);
  }
}
