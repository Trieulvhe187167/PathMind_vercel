export type KnowledgeEngineGroup = "local" | "server" | "cloud";

const LOCAL_ENGINE_IDS = new Set(["graphrag", "lightrag"]);

/** Knowledge Center grouping: both built-in engines run locally. */
export function knowledgeEngineGroup(provider: {
  id: string;
  requires_api_key?: boolean;
}): KnowledgeEngineGroup {
  if (LOCAL_ENGINE_IDS.has(provider.id)) return "local";
  return provider.requires_api_key ? "cloud" : "local";
}
