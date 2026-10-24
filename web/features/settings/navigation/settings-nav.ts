"use client";

import {
  AudioLines,
  Bot,
  Boxes,
  Brain,
  BrainCircuit,
  Clapperboard,
  Database,
  FileScan,
  Image as ImageIcon,
  KeyRound,
  Library,
  ListChecks,
  MessagesSquare,
  Mic,
  Palette,
  Search,
  SlidersHorizontal,
  Sparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import {
  ClaudeGlyph,
  CodexGlyph,
  DeepSeekGlyph,
  GeminiGlyph,
  HermesGlyph,
  KimiGlyph,
  MimoGlyph,
  OpenClawGlyph,
  OpencodeGlyph,
} from "@/components/agents/agent-icons";
import type { ServiceName } from "@/features/settings/store/SettingsStore";
import type { SettingsAccess } from "@/features/settings/navigation/settings-access";

/**
 * Settings information architecture.
 *
 * Independent settings pages, with legacy fragment aliases preserved.
 * This module remains the source for labels, visibility,
 * search metadata, and persistence hints.
 */

export type Lang = { zh: string; en: string };

export interface SettingsLeaf {
  key: string;
  href: string;
  label: Lang;
  blurb: Lang;
  icon: LucideIcon;
  /** Colored icon-tile accent for the sub-hub grid (full class strings). */
  tile: string;
  /** Model-service leaves carry a configured/not chip from the catalog. */
  service?: ServiceName;
  /** Hidden from non-admin users (the backend rejects them anyway). */
  adminOnly?: boolean;
  /** Only shown to a signed-in account (multi-user auth enabled). */
  authOnly?: boolean;
}

export interface SettingsCategory {
  key: string;
  label: Lang;
  /** One-line descriptor shown on the hub block. */
  blurb: Lang;
  icon: LucideIcon;
  /** Canonical in-document category anchor. */
  href: string;
  /** Nested anchors (omitted for direct-section categories). */
  children?: SettingsLeaf[];
  /** Hidden from non-admin users. */
  adminOnly?: boolean;
}

export function isSettingsLeafVisible(
  leaf: SettingsLeaf,
  access: SettingsAccess,
): boolean {
  if (leaf.authOnly && !access.authEnabled) return false;
  return !(leaf.adminOnly && access.hideAdminOnly);
}

export function isSettingsCategoryVisible(
  category: SettingsCategory,
  access: SettingsAccess,
): boolean {
  if (!category) return false;
  if (category.adminOnly && access.hideAdminOnly) return false;
  return (
    !category.children ||
    category.children.some((leaf) => isSettingsLeafVisible(leaf, access))
  );
}

export function visibleSettingsChildren(
  categoryKey: string,
  access: SettingsAccess,
): SettingsLeaf[] {
  return (
    SETTINGS_CATEGORIES.find((category) => category.key === categoryKey)
      ?.children ?? []
  ).filter((leaf) => isSettingsLeafVisible(leaf, access));
}

const MODEL_CHILDREN: SettingsLeaf[] = [
  {
    key: "voice",
    href: "/settings/voice",
    label: { en: "Voice", zh: "语音" },
    blurb: {
      en: "Speech synthesis and transcription models with saved providers.",
      zh: "使用已配置的提供方管理语音合成与语音识别模型。",
    },
    icon: AudioLines,
    tile: "bg-rose-500/10 text-rose-600",
    adminOnly: true,
  },
  {
    key: "multimodal",
    href: "/settings/multimodal",
    label: { en: "Multimodal generation", zh: "多模态生成" },
    blurb: {
      en: "Image and video generation models with saved providers.",
      zh: "使用已配置的提供方管理图片与视频生成模型。",
    },
    icon: ImageIcon,
    tile: "bg-violet-500/10 text-violet-600",
    adminOnly: true,
  },
  {
    key: "connections",
    href: "/settings#connections",
    label: { zh: "提供方", en: "Providers" },
    blurb: {
      zh: "管理提供方的名称、地址、密钥并测试连接。",
      en: "Manage provider names, addresses, credentials, and connectivity.",
    },
    icon: KeyRound,
    tile: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
    adminOnly: true,
  },
  {
    key: "llm",
    href: "/settings#llm",
    label: { zh: "语言模型", en: "Language models" },
    blurb: {
      zh: "模型名称、上下文窗口、能力与连接测试。",
      en: "Model names, context windows, capabilities, and connection tests.",
    },
    icon: Brain,
    tile: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    service: "llm",
    adminOnly: true,
  },
  {
    key: "task-models",
    href: "/settings#task-models",
    label: { zh: "后台任务模型", en: "Task models" },
    blurb: {
      zh: "PathMind 自己发起的调用使用的模型。",
      en: "The model behind the calls PathMind makes on its own.",
    },
    icon: ListChecks,
    tile: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
    adminOnly: true,
  },
  {
    key: "embedding",
    href: "/settings#embedding",
    label: { zh: "嵌入模型", en: "Embedding models" },
    blurb: {
      zh: "嵌入模型、维度与连接测试。",
      en: "Embedding models, dimensions, and connection tests.",
    },
    icon: Database,
    tile: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    service: "embedding",
    adminOnly: true,
  },
  {
    key: "search",
    href: "/settings#search",
    label: { zh: "搜索", en: "Search" },
    blurb: { zh: "联网搜索供应商。", en: "Web search providers." },
    icon: Search,
    tile: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    service: "search",
    adminOnly: true,
  },
  {
    key: "tts",
    href: "/settings#tts",
    label: { zh: "语音合成", en: "Text-to-Speech" },
    blurb: {
      zh: "朗读助手回复的 TTS 供应商。",
      en: "Text-to-speech for reading replies aloud.",
    },
    icon: AudioLines,
    tile: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    service: "tts",
    adminOnly: true,
  },
  {
    key: "stt",
    href: "/settings#stt",
    label: { zh: "语音识别", en: "Speech-to-Text" },
    blurb: {
      zh: "转写麦克风录音的 STT 供应商。",
      en: "Speech-to-text for the composer microphone.",
    },
    icon: Mic,
    tile: "bg-pink-500/10 text-pink-600 dark:text-pink-400",
    service: "stt",
    adminOnly: true,
  },
  {
    key: "imagegen",
    href: "/settings#imagegen",
    label: { zh: "文生图", en: "Image Generation" },
    blurb: {
      zh: "chat imagegen 工具使用的文生图模型。",
      en: "Text-to-image model for the chat imagegen tool.",
    },
    icon: ImageIcon,
    tile: "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
    service: "imagegen",
    adminOnly: true,
  },
  {
    key: "videogen",
    href: "/settings#videogen",
    label: { zh: "文生视频", en: "Video Generation" },
    blurb: {
      zh: "chat videogen 工具使用的文生视频模型。",
      en: "Text-to-video model for the chat videogen tool.",
    },
    icon: Clapperboard,
    tile: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    service: "videogen",
    adminOnly: true,
  },
];

const CHAT_CHILDREN: SettingsLeaf[] = [
  {
    key: "tools",
    href: "/settings#tools",
    label: { zh: "工具", en: "Tools" },
    blurb: {
      zh: "对话智能体可调用的内置工具。",
      en: "Built-in tools the chat agent can invoke.",
    },
    icon: Wrench,
    tile: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
  },
  {
    key: "capabilities",
    href: "/settings#capabilities",
    label: { zh: "能力", en: "Capabilities" },
    blurb: {
      zh: "各能力的 LLM 参数与运行时旋钮。",
      en: "Per-capability LLM parameters and runtime knobs.",
    },
    icon: SlidersHorizontal,
    tile: "bg-lime-500/10 text-lime-600 dark:text-lime-400",
    adminOnly: true,
  },
  {
    key: "starters",
    href: "/settings#starters",
    label: { zh: "起始建议", en: "Starting points" },
    blurb: {
      zh: "主页输入框下方那三行引导的素材范围。",
      en: "How much history shapes the three lines under the composer.",
    },
    icon: Sparkles,
    tile: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
];

export const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    key: "appearance",
    label: { zh: "外观", en: "Appearance" },
    blurb: { zh: "视觉主题与代码块", en: "Theme and code blocks" },
    icon: Palette,
    href: "/settings#appearance",
  },
  {
    key: "models",
    adminOnly: true,
    label: { zh: "模型", en: "Models" },
    blurb: {
      zh: "语言、向量、搜索、语音与生成模型",
      en: "Language, embedding, search, voice, and generation models",
    },
    icon: Boxes,
    href: "/settings#models",
    children: MODEL_CHILDREN,
  },
  {
    key: "knowledge",
    adminOnly: true,
    label: { zh: "知识库", en: "Knowledge Base" },
    blurb: { zh: "文档解析引擎", en: "Document parsing engine" },
    icon: Library,
    href: "/settings#document-parsing",
  },
  {
    key: "chat",
    label: { zh: "聊天", en: "Chat" },
    blurb: {
      zh: "工具、能力与起始建议",
      en: "Tools, capabilities, and starting points",
    },
    icon: MessagesSquare,
    href: "/settings#chat",
    children: CHAT_CHILDREN,
  },
  {
    key: "memory",
    // Deployment-wide knobs (main.yaml); the backend only accepts admin writes.
    adminOnly: true,
    label: { zh: "记忆", en: "Memory" },
    blurb: {
      zh: "分块、预算、去重与引用策略",
      en: "Chunking, budget, dedup, and reference policies",
    },
    icon: BrainCircuit,
    href: "/settings#memory",
  },
];

export const SETTINGS_HUB_HREF = "/settings";

/** Stable aliases keep existing bookmarks and links from older clients working. */
export const SETTINGS_ALIASES: Record<string, string> = {
  tts: "voice",
  stt: "voice",
  imagegen: "multimodal",
  videogen: "multimodal",
  overview: "general",
  about: "general",
  models: "llm",
  chat: "starters",
  "document-parsing": "knowledge",
  image: "multimodal",
  video: "multimodal",
  // Removed for the hosted site (server ports, server folders, desktop data
  // migration, self-hosted Invidious): old links land on General.
  network: "general",
  workspace: "general",
  "data-migration": "general",
  "video-learning": "general",
};

export function resolveSettingsKey(key: string): string {
  return SETTINGS_ALIASES[key] ?? key;
}

/** Kept as an API name for callers; URLs now address independent pages. */
export function settingsAnchorHref(key: string): string {
  return `${SETTINGS_HUB_HREF}/${resolveSettingsKey(key)}`;
}

// The on-disk file (under data/user/settings/) each leaf module persists to.
// Surfaced in the toolbar status line so every page says where its parameters
// live, without duplicating the string on each page. Singleton pages (no
// merged category) are keyed by pathname; leaves inside a merged category
// page share one pathname, so those are keyed by `leaf.key` instead and
// looked up via the currently scrolled-to section (see `storagePathFor`).
const STORAGE_PATHS: Record<string, string> = {
  "/settings#appearance": "data/user/settings/interface.json",
  "/settings#llm": "data/user/settings/model_catalog.json",
  "/settings#embedding": "data/user/settings/model_catalog.json",
  "/settings#search": "data/user/settings/model_catalog.json",
  "/settings#tts": "data/user/settings/model_catalog.json",
  "/settings#stt": "data/user/settings/model_catalog.json",
  "/settings#image": "data/user/settings/model_catalog.json",
  "/settings#video": "data/user/settings/model_catalog.json",
  "/settings#document-parsing": "data/user/settings/document_parsing.json",
  "/settings#memory": "data/user/settings/main.yaml",
  appearance: "data/user/settings/interface.json",
  voice: "data/user/settings/model_catalog.json",
  multimodal: "data/user/settings/model_catalog.json",
  connections: "data/user/settings/model_catalog.json",
  "task-models": "data/user/settings/model_catalog.json",
  knowledge: "data/user/settings/document_parsing.json",
  starters: "data/user/settings/interface.json",
  memory: "data/user/settings/main.yaml",
  llm: "data/user/settings/model_catalog.json",
  embedding: "data/user/settings/model_catalog.json",
  search: "data/user/settings/model_catalog.json",
  tts: "data/user/settings/model_catalog.json",
  stt: "data/user/settings/model_catalog.json",
  imagegen: "data/user/settings/model_catalog.json",
  videogen: "data/user/settings/model_catalog.json",
  tools: "data/user/settings/interface.json",
  capabilities: "data/user/settings/main.yaml · agents.yaml",
};

export function storagePathFor(
  pathname: string,
  activeSection?: string | null,
): string | null {
  if (pathname === SETTINGS_HUB_HREF) {
    return activeSection ? (STORAGE_PATHS[activeSection] ?? null) : null;
  }
  const key = resolveSettingsKey(pathname.replace(/^\/settings[\/#]?/, ""));
  if (key === "general") return "data/user/settings/interface.json";
  return STORAGE_PATHS[key] ?? STORAGE_PATHS[pathname] ?? null;
}
