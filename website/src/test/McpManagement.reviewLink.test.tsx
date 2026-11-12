// The paused-share bell note links to the Sharing view with `?review=1`.
//
// Contract under test:
// - that link opens the Review dialog over the waiting servers, with no click,
//   and drops the param so a reload does not reopen the dialog
// - without the param nothing opens
// - with the param and no server waiting nothing opens
// - the note reads in the dashboard's language, with the server names filled in
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api/client'
import { noteText } from '../components/notifications/notifMeta'
import { i18nT } from '../i18n/t'
import { McpManagement } from '../pages/settings/McpManagement'
import type { Notification } from '../types'
import { answerBatchPreviewsPerName } from './McpManagementTestUtils'

const WAIT = { timeout: 5000 }
const M = (key: string, vars?: Record<string, unknown>) => i18nT(`pages.mcpManagement.${key}`, vars)

const server = {
  name: 'alpha-mcp',
  stub: true,
  can_stub: true,
  in_allowlist: true,
  entry_poolable: false,
  agents: ['kirocrew'],
  transport: 'stdio',
  denylisted: false,
}

const changed = {
  reason: 'changed_needs_reapproval',
  commands: [['npx', '-y', 'alpha-mcp@2']],
  envs: [['MODE=fast']],
  approved_commands: [['npx', '-y', 'alpha-mcp@1']],
  approved_envs: [['MODE=fast']],
  complete: true,
  expected_launch: 'a:b',
}

const status = (launch_refused: Record<string, unknown>) => ({
  enabled: true,
  stub: ['alpha-mcp'],
  stub_count: 1,
  running: true,
  ping_ok: true,
  supported: true,
  launch_refused,
})

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}

function mount(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={qc}>
        <McpManagement />
        <Where />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'mcpGatewayFootprint').mockResolvedValue({ using_now_bytes: null, with_sharing_bytes: null, open_chats: 0, stale_chats: [] } as never)
  vi.spyOn(api, 'mcpGatewayServers').mockResolvedValue({ servers: [server] } as never)
  answerBatchPreviewsPerName()
})
afterEach(cleanup)

describe('the paused-share note link', () => {
  it('opens the Review dialog and drops the param', async () => {
    vi.spyOn(api, 'mcpGatewayStatus').mockResolvedValue(status({ 'alpha-mcp': changed }) as never)
    mount('/mcp?view=sharing&review=1')
    const dialog = await screen.findByRole('dialog', undefined, WAIT)
    expect(within(dialog).getByRole('button', { name: M('approve_button') })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/mcp?view=sharing'), WAIT)
    expect(screen.getByTestId('where').textContent).toBe('/mcp?view=sharing')
  })

  it('opens nothing without the param', async () => {
    vi.spyOn(api, 'mcpGatewayStatus').mockResolvedValue(status({ 'alpha-mcp': changed }) as never)
    mount('/mcp?view=sharing')
    // The approval card is the barrier: the waiting servers have been read.
    await screen.findByRole('button', { name: M('review_approve') }, WAIT)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens nothing when no server waits', async () => {
    const read = vi.spyOn(api, 'mcpGatewayStatus').mockResolvedValue(status({}) as never)
    mount('/mcp?view=sharing&review=1')
    // The row's name is the barrier: the server list and the status have been read.
    await screen.findAllByText('alpha-mcp', undefined, WAIT)
    await waitFor(() => expect(read).toHaveBeenCalled(), WAIT)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('the paused-share note copy', () => {
  it('reads from the catalog with the server names filled in', () => {
    const note = {
      title: 'A shared MCP server is paused',
      body: 'stored English',
      i18n: { id: 'mcp_share_paused', params: { names: 'alpha-mcp, beta-mcp' } },
    } as unknown as Notification
    expect(noteText(note)).toEqual({
      title: 'A shared MCP server is paused',
      body: 'Paused: alpha-mcp, beta-mcp. Review and approve the update to share again.',
    })
  })
})
