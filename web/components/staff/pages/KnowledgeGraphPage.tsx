"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import KnowledgeTreePage from "@/components/knowledge-tree/KnowledgeTreePage";
import GraphDiagram from "@/components/knowledge-tree/GraphDiagram";
import GraphSchemaPanel from "@/components/knowledge-tree/GraphSchemaPanel";
import TreeHistoryPanel from "@/components/knowledge-tree/TreeHistoryPanel";
import StaffPage from "@/components/staff/StaffPage";
import { getRights } from "@/lib/knowledge-tree-api";

type Tab = "tree" | "diagram" | "schema" | "history";

export default function StaffKnowledgePage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("tree");
  // Remount the editor after a revert so it shows the restored tree.
  const [treeKey, setTreeKey] = useState(0);
  const [canEdit, setCanEdit] = useState(false);
  useEffect(() => {
    let alive = true;
    getRights()
      .then((r) => alive && setCanEdit(r.can_edit))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  const tabs: { id: Tab; label: string }[] = [
    { id: "tree", label: t("Graph & documents") },
    { id: "diagram", label: t("Diagram") },
    { id: "schema", label: t("Schema") },
    { id: "history", label: t("Edit history") },
  ];
  return (
    <StaffPage
      wide
      title={t("Knowledge graph")}
      subtitle={
        canEdit
          ? t("The main knowledge graph: typed nodes and relations the tutor reasons over, its documents and import/export. Only R&D changes it.")
          : t("The main knowledge graph as R&D curates it. You can read it, its documents and every edit, but not change it.")
      }
    >
      <div role="tablist" className="mb-4 flex gap-1.5">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={`rounded-lg px-3 py-1.5 text-[12px] transition ${
              tab === item.id
                ? "bg-[var(--card)] font-semibold text-[var(--foreground)] shadow-sm"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "tree" && <KnowledgeTreePage key={treeKey} />}
      {tab === "diagram" && <GraphDiagram key={treeKey} />}
      {tab === "schema" && <GraphSchemaPanel canEdit={canEdit} />}
      {tab === "history" && (
        <TreeHistoryPanel canRevert={canEdit} onReverted={() => setTreeKey((k) => k + 1)} />
      )}
    </StaffPage>
  );
}
