import { expect, test, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const captureDir = resolve('artifacts/screenshots/windows')

async function capture(page: Page, name: string) {
  await mkdir(captureDir, { recursive: true })
  await page.waitForTimeout(350)
  await page.screenshot({ path: resolve(captureDir, `${name}.png`), fullPage: true })
}

async function expectCleanPage(page: Page) {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  return () => expect(errors, 'browser console/page errors').toEqual([])
}

test('Windows existing-Docker journey installs on a custom Traefik port', async ({ page }) => {
  const assertClean = await expectCleanPage(page)
  await page.goto('/?platform=windows')
  await expect(page.getByRole('heading', { name: 'Choose your Brain4All version' })).toBeVisible()
  await capture(page, '01-edition')

  await page.getByRole('button', { name: /Web Version/ }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await capture(page, '02-docker-path')

  await page.getByRole('button', { name: /Use existing Docker/ }).click()
  await page.getByRole('button', { name: /Check my system/ }).click()
  await expect(page.getByText('Docker 29.6.2 is running')).toBeVisible()
  await capture(page, '03-system-check')

  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('radio', { name: /Custom port/ }).click()
  await page.getByRole('spinbutton', { name: 'Custom port' }).fill('5252')
  await page.getByRole('button', { name: 'Check port' }).click()
  await expect(page.getByText('Port 5252 is available on this computer.')).toBeVisible()
  await capture(page, '04-custom-port')

  await page.getByRole('button', { name: 'Review' }).click()
  await expect(page.getByText('http://localhost:5252')).toBeVisible()
  await capture(page, '05-review')

  await page.getByRole('button', { name: /Install Brain4All/ }).click()
  await expect(page.getByRole('heading', { name: 'Installing Brain4All' })).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', /\d+/)
  await capture(page, '06-installing')
  await expect(page.getByRole('heading', { name: 'Brain4All is ready' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('http://localhost:5252')).toBeVisible()
  await capture(page, '07-ready')

  await page.getByRole('button', { name: 'Manage Docker' }).click()
  const embedded = page.frameLocator('iframe[title="Brain4All Web application"]')
  await expect(embedded.getByRole('heading', { name: 'What can I help you build?' })).toBeVisible()
  await capture(page, '10-embedded-application')
  await page.getByRole('button', { name: 'Docker Web settings' }).click()
  await capture(page, '11-settings-menu')
  await page.getByRole('menuitem', { name: /System/ }).click()
  await expect(page.getByRole('heading', { name: 'Docker runtime' })).toBeVisible()
  await expect(page.getByText('1 · Traefik only')).toBeVisible()
  await capture(page, '12-system-overlay')
  await page.getByRole('button', { name: 'Close settings' }).click()
  await page.getByRole('button', { name: 'Docker Web settings' }).click()
  await page.getByRole('menuitem', { name: /Logs/ }).click()
  await expect(page.getByText(/Configuration loaded from Docker provider/)).toBeVisible()
  await capture(page, '13-logs-overlay')
  await page.getByRole('button', { name: 'Close settings' }).click()
  await page.getByRole('button', { name: 'Docker Web settings' }).click()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Brain4All Web is stopped' })).toBeVisible()
  await capture(page, '14-application-stopped')
  await page.getByRole('button', { name: 'Docker Web settings' }).click()
  await page.getByRole('button', { name: 'Start', exact: true }).click()
  await expect(page.frameLocator('iframe[title="Brain4All Web application"]').getByRole('heading', { name: 'What can I help you build?' })).toBeVisible()
  assertClean()
})

test('installed Windows app relaunches directly into management', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('brain4all-demo-port', '6252')
    window.localStorage.setItem('brain4all-demo-state', 'running')
  })
  await page.goto('/?platform=windows')
  await expect(page.frameLocator('iframe[title="Brain4All Web application"]').getByRole('heading', { name: 'What can I help you build?' })).toBeVisible()
  await expect(page.getByText('http://localhost:6252')).toBeVisible()
})

test('Windows Docker prerequisite failure is actionable', async ({ page }) => {
  const assertClean = await expectCleanPage(page)
  await page.goto('/?platform=windows&scenario=docker-missing')
  await page.getByRole('button', { name: /Web Version/ }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: /Install Docker/ }).click()
  await page.getByRole('button', { name: /Check my system/ }).click()
  await expect(page.getByText('Docker needs attention')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
  await capture(page, '08-docker-required')
  assertClean()
})

test('Windows occupied port remains on the port screen', async ({ page }) => {
  const assertClean = await expectCleanPage(page)
  await page.goto('/?platform=windows&scenario=port-busy')
  await page.getByRole('button', { name: /Web Version/ }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: /Use existing Docker/ }).click()
  await page.getByRole('button', { name: /Check my system/ }).click()
  await expect(page.getByText('Docker 29.6.2 is running')).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: 'Check port' }).click()
  await expect(page.getByText(/Port 5152 is already used/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Review' })).toBeDisabled()
  await capture(page, '09-port-conflict')
  assertClean()
})
