import { LEARNING_HUB } from '@/lib/learning-routes'
import {
  GitBranch,
  GraduationCap,
  HeartHandshake,
  House,
  LayoutGrid,
  MessagesSquare,
  PenLine,
  School,
  Settings,
  type LucideIcon,
} from 'lucide-react'

import type { Capability } from '@/lib/capability-routes'
import type { PlanRequirement } from '@/lib/plan-routes'

export interface NavEntry {
  href: string
  label: string
  icon: LucideIcon
  tooltipKey?: string
  defaultCollapsed?: boolean
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability
  /** Plan entitlement this feature needs; shown locked with an upgrade link. */
  plan?: PlanRequirement
  /** Only these roles see the entry (undefined = everyone). */
  roles?: Array<'admin' | 'rnd' | 'educator' | 'user'>
}

/**
 * The workspace features, in the order they ship in.
 *
 * This is the *default* arrangement, not the rendered one — a learner can
 * reorder these and fold the ones they don't use into "More"
 * (``lib/sidebar-layout.ts``). Adding an entry here places it for everyone,
 * including people who have already arranged their sidebar: it arrives next to
 * the neighbour it follows below rather than at the bottom of their list.
 */
export const PRIMARY_NAV: NavEntry[] = [
  { href: '/chat', label: 'Home', icon: House, tooltipKey: 'Home tooltip', requires: 'llm' },
  {
    href: '/partners',
    label: 'Partners',
    icon: HeartHandshake,
    tooltipKey: 'Partners tooltip',
    requires: 'llm',
  },
  {
    href: LEARNING_HUB,
    label: 'Personalized Learning',
    icon: GraduationCap,
    tooltipKey: 'One tutor, your own way to learn.',
  },
  { href: '/space', label: 'Learning Space', icon: LayoutGrid, tooltipKey: 'Space tooltip' },
  {
    href: '/courses',
    label: 'Courses',
    icon: School,
    tooltipKey: 'Courses tooltip',
    plan: { feature: 'courses' },
  },
  {
    href: '/knowledge-branch',
    label: 'My Knowledge',
    icon: GitBranch,
    tooltipKey: 'My Knowledge tooltip',
    // Every learner and educator has a personal branch (it grows from their own
    // uploads). R&D and administrators work on the main tree in the staff console.
    roles: ['educator', 'user'],
  },
  {
    href: '/co-writer',
    label: 'Co-Writer',
    icon: PenLine,
    tooltipKey: 'Co-Writer tooltip',
    requires: 'llm',
  },
  {
    href: '/community',
    label: 'Community',
    icon: MessagesSquare,
    tooltipKey: 'Community tooltip',
    plan: { feature: 'community' },
  },
]

export const SECONDARY_NAV: NavEntry[] = [{ href: '/settings', label: 'Settings', icon: Settings }]

export const DEFAULT_COLLAPSED_NAV = PRIMARY_NAV.filter(entry => entry.defaultCollapsed).map(
  entry => entry.href
)

export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map(entry => entry.href)

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV].map(entry => [entry.href, entry])
)

export function isNavActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`)
}
