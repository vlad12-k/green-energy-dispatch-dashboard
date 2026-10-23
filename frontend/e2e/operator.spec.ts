import { mkdir, readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

function contrastRatio(foreground: string, background: string): number {
  const luminance = (colour: string) => {
    const channels = colour.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number);
    if (!channels || channels.length !== 3) throw new Error(`Unexpected computed colour: ${colour}`);
    const linear = channels.map((channel) => {
      const value = channel / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
  };
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0]! + 0.05) / (values[1]! + 0.05);
}

test('small rail labels meet WCAG AA contrast on the dark rail', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const colours = await page.evaluate(() => {
    const rail = document.querySelector('.side-rail');
    const label = document.querySelector('.rail-label');
    const version = document.querySelector('.rail-version');
    if (!(rail instanceof HTMLElement) || !(label instanceof HTMLElement) || !(version instanceof HTMLElement)) {
      throw new Error('Expected rail labels are missing');
    }
    return {
      railBackground: getComputedStyle(rail).backgroundColor,
      labelForeground: getComputedStyle(label).color,
      versionForeground: getComputedStyle(version).color,
    };
  });
  expect(contrastRatio(colours.labelForeground, colours.railBackground)).toBeGreaterThanOrEqual(4.5);
  expect(contrastRatio(colours.versionForeground, colours.railBackground)).toBeGreaterThanOrEqual(4.5);
});

test('mobile topbar uses a readable compact status without wrapping or clipping', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const compactStatus = page.locator('.status-chip-label-compact');
  await expect(compactStatus).toBeVisible();
  await expect(compactStatus).toHaveText('EDITED');
  await expect(page.locator('.status-chip')).toHaveCSS('white-space', 'nowrap');
  const fitsTopbar = await page.evaluate(() => {
    const topbar = document.querySelector('.topbar');
    const actions = document.querySelector('.topbar-right');
    if (!(topbar instanceof HTMLElement) || !(actions instanceof HTMLElement)) return false;
    const topbarBounds = topbar.getBoundingClientRect();
    const actionBounds = actions.getBoundingClientRect();
    return actions.scrollWidth <= actions.clientWidth && actionBounds.right <= topbarBounds.right;
  });
  expect(fitsTopbar).toBe(true);
});

test('mobile carbon chart labels remain legible without page overflow', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByRole('heading', { name: 'Schedule comparison' })).toBeVisible();

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const chartText = await page.locator('.data-chart text').evaluateAll((elements) => elements.map((element) => ({
      fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
      renderedHeight: element.getBoundingClientRect().height,
    })));
    expect(chartText.length).toBeGreaterThan(0);
    for (const label of chartText) {
      expect(label.fontSize, `chart text font at ${width}px`).toBeGreaterThanOrEqual(9);
      expect(label.renderedHeight, `rendered chart text at ${width}px`).toBeGreaterThanOrEqual(7);
    }

    const chartUnits = await page.locator('.chart-unit').evaluateAll((elements) => elements.map(
      (element) => Number.parseFloat(getComputedStyle(element).fontSize),
    ));
    expect(chartUnits.length).toBeGreaterThan(0);
    expect(Math.min(...chartUnits), `chart unit font at ${width}px`).toBeGreaterThanOrEqual(9);

    const chartKeys = await page.locator('.chart-key span').evaluateAll((elements) => elements.map(
      (element) => Number.parseFloat(getComputedStyle(element).fontSize),
    ));
    expect(chartKeys.length).toBeGreaterThan(0);
    expect(Math.min(...chartKeys), `chart key font at ${width}px`).toBeGreaterThanOrEqual(9);

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }

  const tableSummaries = await page.locator('.data-table-details > summary').all();
  expect(tableSummaries).toHaveLength(5);
  for (const summary of tableSummaries) await summary.click();
  const tableRegions = page.getByRole('region', { name: /data table; horizontally scrollable/ });
  await expect(tableRegions).toHaveCount(5);

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const tableFontSizes = await tableRegions.locator('table').evaluateAll((elements) => elements.map(
      (element) => Number.parseFloat(getComputedStyle(element).fontSize),
    ));
    expect(tableFontSizes).toHaveLength(5);
    expect(Math.min(...tableFontSizes), `data table font at ${width}px`).toBeGreaterThanOrEqual(9);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }

  const firstSummary = page.locator('.data-table-details > summary').first();
  const firstTableRegion = page.getByRole('region', {
    name: 'Carbon intensity profile data table; horizontally scrollable',
  });
  await firstSummary.focus();
  await page.keyboard.press('Tab');
  await expect(firstTableRegion).toBeFocused();
  await expect(firstTableRegion).toHaveCSS('outline-style', 'solid');
});

test('simulation flow stays fully visible at intermediate viewport widths', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  const flowMap = page.locator('.flow-map');
  await expect(flowMap).toBeVisible();

  await expect(flowMap.locator('.flow-output')).toHaveCount(2);
  const clippedAtWidths: Array<{ width: number; nodes: string[] }> = [];
  for (const width of [760, 761, 800, 1120, 1121, 1200, 1280, 1281]) {
    await page.setViewportSize({ width, height: 900 });
    for (const node of await flowMap.locator('.flow-input, .flow-engine, .flow-outputs, .flow-output').all()) {
      await expect(node).toBeVisible();
    }
    const clippedNodes = await flowMap.evaluate((map) => {
      const mapBounds = map.getBoundingClientRect();
      const panel = map.closest('.dispatch-flow');
      if (!(panel instanceof HTMLElement)) throw new Error('The flow map must be inside its clipping panel');
      const panelBounds = panel.getBoundingClientRect();
      return [map, ...Array.from(map.querySelectorAll<HTMLElement>(
        '.flow-input, .flow-engine, .flow-connector, .flow-outputs, .flow-output',
      ))]
        .filter((node) => {
          const bounds = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return style.display === 'none'
            || style.visibility === 'hidden'
            || bounds.width <= 0
            || bounds.height <= 0
            || bounds.left < mapBounds.left - 1
            || bounds.right > mapBounds.right + 1
            || bounds.top < mapBounds.top - 1
            || bounds.bottom > mapBounds.bottom + 1
            || bounds.left < panelBounds.left - 1
            || bounds.right > panelBounds.right + 1
            || bounds.top < panelBounds.top - 1
            || bounds.bottom > panelBounds.bottom + 1;
        })
        .map((node) => `${node.className} (${Math.round(node.getBoundingClientRect().width)}×${Math.round(node.getBoundingClientRect().height)})`);
    });
    if (clippedNodes.length > 0) clippedAtWidths.push({ width, nodes: clippedNodes });
  }

  expect(clippedAtWidths, `flow nodes clipped: ${JSON.stringify(clippedAtWidths)}`).toEqual([]);
});

test('edits, evaluates against FastAPI, inspects results, and exports the scenario', async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  await expect(page.getByRole('heading', { name: 'Dispatch workbench' })).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(11, 13, 19)');
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#0B0D13');
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/favicon.svg');
  await expect(page.getByText('No live NESO signal used')).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Workspace sections' })).toBeVisible();
  const shellColumns = await page.locator('.app-shell').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  expect(shellColumns).toBe(2);
  const desktopColumns = await page.locator('.workbench-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  expect(desktopColumns).toBe(2);
  await page.keyboard.press('Tab');
  const brandLink = page.getByRole('link', { name: 'Green Energy Dispatch, Dispatch Workbench' });
  await expect(brandLink).toBeFocused();
  await expect(brandLink).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Tab');
  const scenarioNavLink = page.getByRole('link', { name: 'Scenario' });
  await expect(scenarioNavLink).toBeFocused();
  await page.keyboard.press('Tab');
  const resultsNavLink = page.getByRole('link', { name: 'Analysis' });
  await expect(resultsNavLink).toBeFocused();
  await scenarioNavLink.click();
  await expect(page).toHaveURL(/#scenario-heading$/);
  await resultsNavLink.click();
  await expect(page).toHaveURL(/#results-pane$/);
  await page.getByRole('button', { name: 'Load synthetic example' }).click();
  await page.getByLabel('Required energy (kWh)').fill('8.5');
  await page.locator('input[id="carbon_intensity_profile.1.intensity_value"]').fill('50');
  await page.getByRole('button', { name: 'Run simulation' }).click();

  await expect(page.getByRole('heading', { name: 'Schedule comparison' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Scenario-to-schedule flow' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Interval and constraint detail' })).toBeVisible();
  await expect(page.locator('.schedule-workspace').getByRole('heading', { name: 'Interval and constraint detail' })).toBeVisible();
  const carbonPanelHeight = await page.locator('.intensity-panel').evaluate((element) => element.getBoundingClientRect().height);
  const flowPanelHeight = await page.locator('.dispatch-flow').evaluate((element) => element.getBoundingClientRect().height);
  expect(carbonPanelHeight).toBeLessThan(flowPanelHeight);
  await expect(page.locator('.strategy-proxy-baseline')).toContainText('2,875 gCO2');
  await expect(page.locator('.strategy-proxy-carbon')).toContainText('950 gCO2');
  await expect(page.locator('.difference-value')).toContainText('+1,925 gCO2');
  await expect(page.locator('.strategy-proxy')).toHaveCount(2);
  await expect(page.getByText('Complete schedule demonstrated').first()).toBeVisible();
  await expect(page.getByText('View allocation data').first()).toBeVisible();
  await expect(page.getByText('View load data').first()).toBeVisible();

  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    document.documentElement.style.scrollBehavior = 'auto';
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect(await page.locator('.topbar').evaluate((element) => element.getBoundingClientRect().top)).toBe(0);
  await page.waitForTimeout(100);
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS === '1') {
    await mkdir('../docs/screenshots', { recursive: true });
    await page.screenshot({ path: '../docs/screenshots/ev-depot-workbench-after-1440x900.png', fullPage: false, animations: 'disabled' });
    await page.screenshot({ path: '../docs/screenshots/ev-depot-workbench-after-full-page.png', fullPage: true, animations: 'disabled' });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Dispatch workbench' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Workspace sections' })).toBeVisible();
  await expect(page.locator('.status-chip-label-compact')).toHaveText('COMPLETE');
  const mobileColumns = await page.locator('.workbench-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  expect(mobileColumns).toBe(1);
  const mobileShellDisplay = await page.locator('.app-shell').evaluate((element) => getComputedStyle(element).display);
  expect(mobileShellDisplay).toBe('block');
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS === '1') {
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      document.documentElement.style.scrollBehavior = 'auto';
      window.scrollTo({ top: 0, behavior: 'instant' });
    });
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.locator('.side-rail').evaluate((element) => element.getBoundingClientRect().top)).toBe(0);
    const mobileTopbarTop = await page.locator('.topbar').evaluate((element) => element.getBoundingClientRect().top);
    expect(mobileTopbarTop).toBeGreaterThan(0);
    expect(mobileTopbarTop).toBeLessThan(100);
    await page.waitForTimeout(100);
    await page.screenshot({ path: '../docs/screenshots/ev-depot-workbench-after-390x844.png', fullPage: false, animations: 'disabled' });
    await page.evaluate(() => {
      const heading = document.getElementById('results-heading');
      if (heading) {
        document.documentElement.style.scrollBehavior = 'auto';
        window.scrollTo({ top: heading.getBoundingClientRect().top + window.scrollY - 148, behavior: 'instant' });
      }
    });
    const mobileResultsHeadingTop = await page.getByRole('heading', { name: 'Schedule comparison' }).evaluate((element) => element.getBoundingClientRect().top);
    expect(mobileResultsHeadingTop).toBeGreaterThanOrEqual(140);
    expect(mobileResultsHeadingTop).toBeLessThan(160);
    await page.waitForTimeout(100);
    await page.screenshot({ path: '../docs/screenshots/ev-depot-workbench-after-mobile-results-390x844.png', fullPage: false, animations: 'disabled' });
  }
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(hasHorizontalOverflow).toBe(false);

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('synthetic-carbon-shift-demo');
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  const exported = JSON.parse(await readFile(downloadedPath!, 'utf-8')) as {
    vehicles: Array<{ required_energy_kwh: number }>;
    carbon_intensity_profile: Array<{ intensity_value: number }>;
  };
  expect(exported.vehicles[0]?.required_energy_kwh).toBe(8.5);
  expect(exported.carbon_intensity_profile[1]?.intensity_value).toBe(50);

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('html')).toHaveCSS('scroll-behavior', 'auto');
  const reducedTransitionSeconds = Number.parseFloat(await page.locator('.button-primary').first().evaluate((element) => getComputedStyle(element).transitionDuration));
  expect(reducedTransitionSeconds).toBeLessThan(0.001);
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});
