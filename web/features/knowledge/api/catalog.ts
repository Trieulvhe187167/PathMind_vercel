export {
  connectLinkedFolder,
  connectMarginNote4Library,
  connectObsidianVault,
  createKnowledgeBase,
  deleteKnowledgeBase,
  getKnowledgeUploadPolicy,
  invalidateKnowledgeCaches,
  listKnowledgeBases,
  listRagProviders,
  probeLinkedFolder,
  readErrorDetail,
  reindexKnowledgeBase,
  retryKnowledgeBase,
  setDefaultKnowledgeBase,
  updatePendingIndexingPolicy,
} from "./client";

export type {
  IndexingLLMSelection,
  KnowledgeBaseSummary,
  KnowledgeTaskResponse,
  KnowledgeUploadPolicy,
  LinkedFolderProbe,
  RagProviderSummary,
} from "../model/types";
