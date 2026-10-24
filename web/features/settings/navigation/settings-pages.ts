import { Activity, Archive, CreditCard, Settings2, UserRound } from 'lucide-react'
import {
  SETTINGS_CATEGORIES,
  isSettingsCategoryVisible,
  isSettingsLeafVisible,
  resolveSettingsKey,
  type Lang,
  type SettingsLeaf,
} from './settings-nav'
import type { LucideIcon } from 'lucide-react'
import type { SettingsAccess } from './settings-access'

export const SETTINGS_PAGE_GROUPS: { label: Lang; keys: string[] }[] = [
  {
    label: { en: 'Personal', zh: '个人' },
    keys: ['profile', 'billing', 'general', 'appearance'],
  },
  {
    label: { en: 'Learning & conversation', zh: '学习与对话' },
    keys: ['starters', 'memory'],
  },
  {
    label: { en: 'Models & services', zh: '模型与服务' },
    keys: ['connections', 'llm', 'task-models', 'embedding', 'search', 'voice', 'multimodal'],
  },
  {
    label: { en: 'Features & integrations', zh: '功能与集成' },
    keys: ['tools', 'capabilities', 'knowledge'],
  },
  {
    label: { en: 'System', zh: '系统' },
    keys: ['status'],
  },
  { label: { en: 'Archived', zh: '已归档' }, keys: ['archive'] },
]

/**
 * Pages laid out as master–detail (a list rail beside an editor). They need the
 * width for both columns: at the shared 960px cap the editor column came out
 * around 590px, so its fields sat in one cramped stack with hundreds of unused
 * pixels beside them. Text-only preference pages stay narrow on purpose.
 */
const WIDE_SETTINGS_PAGES = new Set([
  'connections',
  'llm',
  'embedding',
  'search',
  'voice',
  'multimodal',
])

export function isWideSettingsPage(key: string): boolean {
  return WIDE_SETTINGS_PAGES.has(resolveSettingsKey(key))
}

/** Related service pages share a compact navigation row, with explicit tabs. */
export const SETTINGS_PAGE_FAMILIES: string[][] = []

export function settingsPageFamily(key: string): string[] {
  return SETTINGS_PAGE_FAMILIES.find(family => family.includes(key)) ?? [key]
}

const extraPages: SettingsLeaf[] = [
  {
    key: 'profile',
    label: { en: 'Profile', zh: '个人资料' },
    blurb: { en: 'Your account and avatar', zh: '你的账号与头像' },
    icon: UserRound,
    href: '/settings/profile',
    tile: '',
    authOnly: true,
  },
  {
    key: 'billing',
    label: { en: 'Plans & billing', zh: '套餐与账单' },
    blurb: {
      en: 'Subscription, credits, storage and payment history',
      zh: '订阅、额度、存储与付款记录',
    },
    icon: CreditCard,
    href: '/settings/billing',
    tile: '',
    authOnly: true,
  },

  {
    key: 'archive',
    label: { en: 'Archived chats', zh: '已归档的聊天' },
    blurb: {
      en: 'Search, unarchive, or permanently delete archived conversations',
      zh: '搜索、取消归档或永久删除已归档的对话',
    },
    icon: Archive,
    href: '/settings/archive',
    tile: '',
  },
  {
    key: 'general',
    label: { en: 'General', zh: '常规' },
    blurb: {
      en: 'Interface language, response language, and setup help',
      zh: '界面语言、回复语言与配置帮助',
    },
    icon: Settings2,
    href: '/settings/general',
    tile: '',
  },
  {
    key: 'status',
    label: { en: 'Runtime status', zh: '运行状态' },
    blurb: {
      en: 'Service readiness, diagnostics, and memory usage',
      zh: '服务就绪情况、诊断与内存占用',
    },
    icon: Activity,
    href: '/settings/status',
    tile: '',
    adminOnly: true,
  },
]

/** Pages that configure the whole system (shown in the admin console, not the app). */
export function isSystemSettingsKey(key: string): boolean {
  const resolved = resolveSettingsKey(key)
  if (extraPages.some(leaf => leaf.adminOnly && resolveSettingsKey(leaf.key) === resolved)) return true
  return SETTINGS_CATEGORIES.some(category =>
    (category.children ?? [{ ...category, tile: '' }]).some(
      leaf => (leaf.adminOnly || category.adminOnly) && resolveSettingsKey(leaf.key) === resolved
    )
  )
}

export function visibleSettingsPages(access: SettingsAccess): SettingsLeaf[] {
  return [
    ...extraPages.filter(leaf => isSettingsLeafVisible(leaf, access)),
    ...SETTINGS_CATEGORIES.filter(category => isSettingsCategoryVisible(category, access))
      .flatMap(category => category.children ?? [{ ...category, tile: '' }])
      .filter(
        leaf => isSettingsLeafVisible(leaf, access) && resolveSettingsKey(leaf.key) === leaf.key
      ),
  ]
}

export function settingsPageLabel(key: string, fallback: Lang): Lang {
  if (key === 'llm') return { en: 'Language models', zh: '语言模型' }
  if (key === 'starters') return { en: 'Conversation', zh: '对话' }
  if (key === 'connections') return { en: 'Providers', zh: '提供方' }
  if (key === 'knowledge') return { en: 'Knowledge & documents', zh: '知识与文档' }
  return fallback
}

export function settingsPageIcon(key: string, fallback: LucideIcon): LucideIcon {
  return fallback
}

/** Old links sometimes placed ?profile after the fragment. Preserve it too. */
export function legacySettingsDestination(hash: string, search: string): string {
  const [rawKey, hashQuery = ''] = hash.replace(/^#/, '').split('?')
  let key = 'general'
  try {
    key = resolveSettingsKey(decodeURIComponent(rawKey || 'general'))
  } catch {
    /* malformed old URL */
  }
  const known = new Set([
    ...extraPages.map(page => page.key),
    ...SETTINGS_CATEGORIES.flatMap(
      category => category.children?.map(leaf => leaf.key) ?? [category.key]
    ),
  ])
  if (!known.has(key)) key = 'general'
  const query = new URLSearchParams(search)
  new URLSearchParams(hashQuery).forEach((value, name) => query.set(name, value))
  return `/settings/${key}${query.size ? `?${query}` : ''}`
}
