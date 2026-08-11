import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { App } from './App'

describe('Docker Web installer UI', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('does not continue without an explicit available edition', async () => {
    const user = userEvent.setup()
    render(<App />)
    const continueButton = screen.getByRole('button', { name: /continue/i })
    expect(continueButton).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /full managed app/i }))
    expect(screen.getByText(/not available yet/i)).toBeVisible()
    expect(continueButton).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /web version/i }))
    expect(continueButton).toBeEnabled()
  })

  it('requires a successful port check before review', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /web version/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /use existing docker/i }))
    await user.click(screen.getByRole('button', { name: /check my system/i }))
    expect(await screen.findByText(/docker 29\.6\.2 is running/i)).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^continue/i }))
    const review = screen.getByRole('button', { name: /review/i })
    expect(review).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /check port/i }))
    expect(await screen.findByText(/port 5152 is available/i)).toBeVisible()
    expect(review).toBeEnabled()
  })
})
