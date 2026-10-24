'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import Modal from '@/components/common/Modal'
import { useChatWorkspaces } from '@/hooks/useChatWorkspaces'
import { activeWorkspaceId, scopedUrl } from '@/lib/workspace-scope'
import type { LearningOrigin } from '@/lib/learning-library'
import type { PlanLimit } from '@/lib/entitlements-api'
import { useEntitlements } from '@/components/access/EntitlementsContext'
import { UpgradeLink, UsageMeter, useLimitReachedText } from '@/components/access/PlanGate'

export function WorkspaceLabel({ row }: { row: LearningOrigin }) {
  const { t } = useTranslation()
  return <span className="text-xs text-muted-foreground">{row.content_workspace_name || (row.content_workspace_id ? row.content_workspace_id : t('Default workspace'))}</span>
}

export function useLibraryFilter<T extends LearningOrigin>(rows: T[]) {
  const [filter, setFilter] = useState('*')
  const { t } = useTranslation()
  const options = new Map(rows.map(row => [row.content_workspace_id ?? '', row.content_workspace_name || row.content_workspace_id || t('Default workspace')]))
  const control = <label className="inline-flex max-w-full shrink-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
    {t('Show materials from')}
    <select aria-label={t('Filter by workspace')} value={filter} onChange={event => setFilter(event.target.value)} className="h-9 min-w-0 max-w-56 rounded-lg border border-border bg-background px-3 text-sm text-foreground">
      <option value="*">{t('All workspaces')}</option>
      {[...options].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
    </select>
  </label>
  return { rows: filter === '*' ? rows : rows.filter(row => (row.content_workspace_id ?? '') === filter), control }
}

/** Pick a destination only when creating; URL scopes all ensuing uploads and writes. */
export function useLearningCreation(open: () => void, limit?: PlanLimit) {
  const router = useRouter()
  const pathname = usePathname()
  const query = useSearchParams()
  const ent = useEntitlements()
  const limitText = useLimitReachedText()
  const [limitOpen, setLimitOpen] = useState(false)
  // A monthly allowance that is used up opens the upgrade note instead.
  const full = !!limit && ent.atLimit(limit)
  const guarded = useMemo(() => (full ? () => setLimitOpen(true) : open), [full, open])
  const openRef = useRef(guarded)
  useEffect(() => { openRef.current = guarded }, [guarded])
  const consumed = useRef(false)
  useEffect(() => {
    if (query.get('create') !== '1') { consumed.current = false; return }
    if (consumed.current) return
    consumed.current = true
    openRef.current()
    const next = new URLSearchParams(query.toString())
    next.delete('create')
    router.replace(`${pathname}${next.size ? `?${next}` : ''}`, { scroll: false })
  }, [pathname, query, router])
  const { t } = useTranslation()
  const { workspaces, error } = useChatWorkspaces()
  const [choosing, setChoosing] = useState(false)
  const [destination, setDestination] = useState(activeWorkspaceId)
  const limitDialog = limit ? <Modal isOpen={limitOpen} onClose={() => setLimitOpen(false)} title={ent.trial ? t('Free trial used') : t('Monthly limit reached')} width="sm">
    <div className="space-y-4 p-5">
      <p className="text-sm text-muted-foreground">{limitText(limit)}</p>
      <p className="text-xs text-muted-foreground">{ent.trial ? t('The free plan includes one of each to try. Everything you already created stays available.') : t('Your allowance resets on the first day of each month. Everything you already created stays available.')}</p>
      <div className="flex justify-end"><UpgradeLink target={limit} /></div>
    </div>
  </Modal> : null
  const dialog = <><Modal isOpen={choosing} onClose={() => setChoosing(false)} title={t('Save new content to')} width="sm">
    <div className="space-y-4 p-5">
      {limit ? <UsageMeter target={limit} label={limit === 'books_per_month' ? 'Books created' : 'Paths created'} monthly /> : null}
      <p className="text-sm text-muted-foreground">{t('Only new content is saved here. Your existing materials remain visible in all workspaces.')}</p>
      <select aria-label={t('Save to workspace')} value={destination} onChange={event => setDestination(event.target.value)} className="w-full rounded-lg border border-border bg-background p-2">
        <option value="">{t('Default workspace')}</option>
        {workspaces.filter(row => !row.archived && row.status === 'ready').map(row => <option key={row.workspace_id} value={row.workspace_id}>{row.display_name}</option>)}
      </select>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <button type="button" className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground" onClick={() => {
        setChoosing(false)
        if (destination === activeWorkspaceId()) guarded()
        else {
          const next = new URLSearchParams(query.toString())
          next.delete('dt_workspace'); next.delete('workspace'); next.set('create', '1')
          router.push(scopedUrl(`${pathname}?${next}`, destination))
        }
      }}>{t('Continue')}</button>
    </div>
  </Modal>{limitDialog}</>
  // Course-created content inherits the course store so its references stay valid.
  return { begin: () => full ? setLimitOpen(true) : query.has("course") ? open() : setChoosing(true), dialog }
}

export function requestedLearningCreation() {
  return typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('create') === '1'
}
