import { mkdir } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import {
  assertFixtureIdentity,
  EXPECTED_FIXTURE_IDENTITY,
  verifyE2EFixtureIdentity,
} from '../scripts/e2e-server-guard.mjs';
import { assertNoE2EExternalRequests, installE2EOriginGuard } from './local-origin-guard';

test.beforeEach(async ({ page }) => {
  await verifyE2EFixtureIdentity();
  await installE2EOriginGuard(page);
});

test.afterEach(async ({ page }) => {
  await verifyE2EFixtureIdentity();
  assertNoE2EExternalRequests(page);
});

test('fixture identity validation refuses an unexpected local server', () => {
  expect(() => assertFixtureIdentity({ ...EXPECTED_FIXTURE_IDENTITY, fixture_id: 'other-server' }))
    .toThrow(/Refusing browser navigation: deterministic E2E fixture identity did not match/);
});

test('E2E API exposes its deterministic fixture identity before browser navigation', async () => {
  expect(await verifyE2EFixtureIdentity()).toEqual(EXPECTED_FIXTURE_IDENTITY);
});

test('forecast-backed workbench uses the local API, shows server provenance, and remains responsive', async ({ page }) => {
  const evaluationPaths: string[] = [];
  const pageErrors: string[] = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'POST' && pathname.startsWith('/api/v1/simulations/ev-depot/')) {
      evaluationPaths.push(pathname);
    }
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const livePanel = page.getByRole('region', { name: 'Live carbon intensity' });
  await expect(livePanel).toContainText('E2E deterministic fixture');
  await page.getByRole('radio', { name: /NESO forecast-backed scenario/i }).check();
  const schedulingStart = await page.getByLabel('Scheduling start (UTC)').inputValue();
  expect(schedulingStart).toMatch(/T\d{2}:(00|30)$/);

  const requestPromise = page.waitForRequest((request) => (
    request.method() === 'POST'
    && new URL(request.url()).pathname === '/api/v1/simulations/ev-depot/forecast-evaluate'
  ));
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v1/simulations/ev-depot/forecast-evaluate'
  ));
  await page.getByRole('button', { name: 'Evaluate forecast schedule' }).click();
  const [request, response] = await Promise.all([requestPromise, responsePromise]);
  expect(response.status()).toBe(200);
  const payload = request.postDataJSON() as Record<string, unknown>;
  expect(Object.keys(payload)).toEqual(['scenario']);
  expect(payload).not.toHaveProperty('forecast');
  expect(payload).not.toHaveProperty('region_id');
  expect(payload.scenario).toMatchObject({ interval_minutes: 30, intervals: 4, depot_power_kw: 20 });
  expect(evaluationPaths).toEqual(['/api/v1/simulations/ev-depot/forecast-evaluate']);

  const result = await response.json() as {
    status: string;
    input_provenance: { region_name: string; measurement_type: string; source_classification: string };
    immediate_baseline: { feasible: boolean };
    forecast_aware: { feasible: boolean };
    advisory_only: boolean;
    measured: boolean;
  };
  expect(result.status).toBe('available');
  expect(result.input_provenance).toMatchObject({
    region_name: 'E2E deterministic fixture',
    measurement_type: 'forecast',
    source_classification: 'regional_forecast',
  });
  expect(result.advisory_only).toBe(true);
  expect(result.measured).toBe(false);

  await expect(page.getByRole('heading', { name: 'Charging strategy comparison' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Immediate Baseline', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Forecast-aware Heuristic', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Forecast provenance' })).toBeVisible();
  await expect(page.getByText('E2E deterministic fixture').last()).toBeVisible();
  await expect(page.getByText(/Modelled forecast-weighted carbon proxy only/i)).toBeVisible();
  await page.getByText('View time-aligned power values').click();
  await page.getByText('Vehicle energy delivery, deadlines and allocations').first().click();
  await expect(page.getByRole('table', { name: 'Forecast strategy power by UTC interval' })).toBeAttached();
  await expect(page.getByRole('table', { name: 'Immediate Baseline vehicle energy delivery and deadlines' })).toBeAttached();

  const forecastRequest = page.waitForResponse((candidate) => (
    candidate.request().method() === 'GET'
    && new URL(candidate.url()).pathname === '/api/v1/energy/carbon-intensity/forecast'
    && new URL(candidate.url()).searchParams.get('region_id') === '13'
  ));
  await page.getByRole('button', { name: 'Load regional forecast preview' }).click();
  const forecastResponse = await forecastRequest;
  expect(forecastResponse.status()).toBe(200);
  const serverDate = forecastResponse.headers().date;
  expect(serverDate, 'the same-origin API response must expose its authoritative HTTP Date').toBeTruthy();
  const timeline = await forecastResponse.json() as {
    region_id: number;
    measurement_type: string;
    units: string;
    retrieved_at: string;
    intervals: Array<{ measurement_type: string; observed_at: string }>;
  };
  expect(timeline.region_id).toBe(13);
  expect(timeline.measurement_type).toBe('forecast');
  expect(timeline.units).toBe('gCO2/kWh');
  expect(timeline.intervals).toHaveLength(48);
  expect(Date.parse(timeline.retrieved_at)).toBeLessThanOrEqual(Date.parse(serverDate!) + 2000);
  expect(timeline.intervals.every((interval) => interval.measurement_type === 'forecast')).toBe(true);
  await expect(page.locator('[data-forecast-interval]')).toHaveCount(48);
  await page.getByText('View forecast interval values').click();
  await expect(page.getByRole('table', { name: 'Regional carbon-intensity forecast by UTC interval' })).toBeAttached();

  const desktopColumns = await page.locator('.workbench-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  expect(desktopColumns).toBe(2);
  const finalPowerTickVisible = await page.locator('.forecast-power-viewport').evaluate((viewport) => {
    const ticks = viewport.querySelectorAll('svg[data-comparison-power-chart] g > text.forecast-chart-label');
    const finalTick = ticks.item(ticks.length - 1);
    if (!(finalTick instanceof SVGGraphicsElement)) return false;
    const tickBounds = finalTick.getBoundingClientRect();
    const viewportBounds = viewport.getBoundingClientRect();
    return tickBounds.width > 0
      && tickBounds.left >= viewportBounds.left
      && tickBounds.right <= viewportBounds.right;
  });
  expect(finalPowerTickVisible, 'the final UTC time tick should remain visible on the desktop chart').toBe(true);
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS === '1') {
    await mkdir('../docs/screenshots', { recursive: true });
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      document.documentElement.style.scrollBehavior = 'auto';
      window.scrollTo({ top: 0, behavior: 'instant' });
    });
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({ path: '../docs/screenshots/forecast-aware-workbench-b3-1440x900.png', animations: 'disabled' });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  const workspaceNavigation = page.getByRole('navigation', { name: 'Workspace sections' });
  await expect(workspaceNavigation.getByRole('link')).toHaveCount(5);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const allLinksFit = await workspaceNavigation.evaluate((navigation) => {
      const bounds = navigation.getBoundingClientRect();
      return Array.from(navigation.querySelectorAll('a')).every((link) => {
        const linkBounds = link.getBoundingClientRect();
        return linkBounds.width > 0
          && linkBounds.left >= bounds.left
          && linkBounds.right <= bounds.right
          && link.scrollWidth <= link.clientWidth;
      });
    });
    expect(allLinksFit, `all workspace links should fit at ${width}px`).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileColumns = await page.locator('.workbench-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  expect(mobileColumns).toBe(1);
  const resultsHeading = page.getByRole('heading', { name: 'Charging strategy comparison' });
  await resultsHeading.evaluate((heading) => heading.scrollIntoView({ block: 'start', behavior: 'instant' }));
  const headingBounds = await resultsHeading.evaluate((heading) => {
    const bounds = heading.getBoundingClientRect();
    const topbar = document.querySelector('.topbar')?.getBoundingClientRect();
    return { top: bounds.top, topbarBottom: topbar?.bottom ?? 0 };
  });
  expect(headingBounds.top).toBeGreaterThanOrEqual(headingBounds.topbarBottom);
  const chartViewport = page.getByRole('region', { name: 'Charging strategy comparison chart; horizontally scrollable' });
  await expect(chartViewport).toBeVisible();
  expect(await chartViewport.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS === '1') {
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      document.documentElement.style.scrollBehavior = 'auto';
      window.scrollTo({ top: 0, behavior: 'instant' });
    });
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({ path: '../docs/screenshots/forecast-aware-workbench-b3-390x844.png', animations: 'disabled' });
    await chartViewport.scrollIntoViewIfNeeded();
    await expect(chartViewport).toBeVisible();
    const chartBounds = await chartViewport.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const topbar = document.querySelector('.topbar')?.getBoundingClientRect();
      return { top: bounds.top, bottom: bounds.bottom, topbarBottom: topbar?.bottom ?? 0 };
    });
    expect(chartBounds.top).toBeGreaterThanOrEqual(chartBounds.topbarBottom);
    expect(chartBounds.bottom).toBeLessThanOrEqual(844);
    await resultsHeading.evaluate((heading) => heading.scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.screenshot({ path: '../docs/screenshots/forecast-aware-workbench-b3-390x844-results.png', animations: 'disabled' });
  }
  expect(pageErrors).toEqual([]);
});
