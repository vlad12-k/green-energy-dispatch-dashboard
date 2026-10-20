import { mkdir, readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

test('edits, evaluates against FastAPI, inspects results, and exports the scenario', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  await expect(page.getByRole('heading', { name: 'Operator dashboard' })).toBeVisible();
  await expect(page.getByText('No live NESO signal used')).toBeVisible();
  await page.keyboard.press('Tab');
  const brandLink = page.getByRole('link', { name: 'Green Energy Dispatch, Operator Dashboard' });
  await expect(brandLink).toBeFocused();
  await expect(brandLink).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeFocused();
  await page.getByRole('button', { name: 'Load synthetic example' }).click();
  await page.getByLabel('Required energy (kWh)').fill('8.5');
  await page.locator('input[id="carbon_intensity_profile.1.intensity_value"]').fill('50');
  await page.getByRole('button', { name: 'Run simulation' }).click();

  await expect(page.getByRole('heading', { name: 'Schedule comparison' })).toBeVisible();
  const proxyValues = page.locator('.comparison-strip .proxy-value');
  await expect(proxyValues.nth(0)).toContainText('2,875 gCO2');
  await expect(proxyValues.nth(1)).toContainText('950 gCO2');
  await expect(proxyValues.nth(2)).toContainText('+1,925 gCO2');
  await expect(page.getByText('Complete schedule demonstrated').first()).toBeVisible();
  await expect(page.getByText('View allocation data').first()).toBeVisible();
  await expect(page.getByText('View load data').first()).toBeVisible();

  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS === '1') {
    await mkdir('../docs/screenshots', { recursive: true });
    await page.screenshot({ path: '../docs/screenshots/operator-dashboard-desktop.png', fullPage: true, animations: 'disabled' });
  }

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

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Operator dashboard' })).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(hasHorizontalOverflow).toBe(false);
});
