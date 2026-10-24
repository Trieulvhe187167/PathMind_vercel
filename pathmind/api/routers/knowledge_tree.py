"""Knowledge graph API. The main graph is hidden: only R&D and administrators read or change it.

See ``services.knowledge_tree``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import audit
from pathmind.services import knowledge_tree as kt

router = APIRouter()


def _who():
    from pathmind.multi_user.models import LOCAL_ADMIN_ID

    u = get_current_user()
    role = str(u.role or "user")
    # Single-user mode (no sign-in): the one local person also curates the tree.
    if str(u.id or "") == LOCAL_ADMIN_ID:
        role = "rnd"
    return str(u.id or ""), str(u.username or ""), role


def _require_view() -> None:
    _, _, role = _who()
    if not kt.can_view(role):
        raise HTTPException(status_code=403, detail="The main knowledge graph is only visible to R&D and administrators.")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except kt.TreeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


class DomainBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class DomainPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    kb_name: str | None = None  # "" or null unlinks; omit to leave unchanged


class NodeBody(BaseModel):
    parent_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    summary: str = ""


class NodePatch(BaseModel):
    title: str | None = None
    summary: str | None = None


class MoveBody(BaseModel):
    parent_id: str | None = None


class AskBody(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    domain_id: str | None = None


@router.get("/me")
def my_rights():
    _, _, role = _who()
    return {"role": role, "can_edit": kt.can_edit(role), "can_view": kt.can_view(role)}


@router.get("/domains")
def list_domains(db: Session = Depends(get_db)):
    _require_view()
    return {"domains": kt.domain_summaries(db)}


@router.post("/domains")
def add_domain(body: DomainBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    d = _run(kt.create_domain, db, role, uid, body.name, body.description)
    return {"id": d.id, "slug": d.slug, "name": d.name}


@router.get("/kb-options")
def kb_options():
    """Administrator's GraphRAG knowledge bases a domain can be linked to."""
    _, _, role = _who()
    if not kt.can_edit(role):
        raise HTTPException(status_code=403, detail="Only R&D can link knowledge bases.")
    options = kt.service.linkable_kbs()
    return {"knowledge_bases": [x["ref"] for x in options], "options": options}


@router.patch("/domains/{domain_id}")
def patch_domain(domain_id: str, body: DomainPatch, db: Session = Depends(get_db)):
    uid, _, role = _who()
    fields = body.model_dump(exclude_unset=True)
    d = _run(
        kt.update_domain, db, role, domain_id, name=fields.get("name"),
        description=fields.get("description"), kb_name=fields.get("kb_name"), set_kb="kb_name" in fields,
        user_id=uid,
    )
    return {"id": d.id, "name": d.name, "kb_name": d.kb_name}


@router.delete("/domains/{domain_id}")
def remove_domain(domain_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    _run(kt.delete_domain, db, role, domain_id, uid)
    return {"ok": True}


@router.get("/domains/{domain_id}/tree")
def get_tree(domain_id: str, db: Session = Depends(get_db)):
    _require_view()
    return _run(kt.tree_of, db, domain_id)


@router.post("/domains/import")
async def import_new_domain(
    file: UploadFile = File(...), name: str = Form(""), db: Session = Depends(get_db)
):
    uid, _, role = _who()
    data = await file.read()
    return _run(
        kt.import_file, db, role, uid, domain_id=None, new_domain_name=name.strip() or None,
        filename=file.filename or "file", data=data,
    )


@router.post("/domains/{domain_id}/import")
async def import_into_domain(
    domain_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    uid, _, role = _who()
    data = await file.read()
    return _run(
        kt.import_file, db, role, uid, domain_id=domain_id, new_domain_name=None,
        filename=file.filename or "file", data=data,
    )


@router.post("/domains/{domain_id}/nodes")
def add_node(domain_id: str, body: NodeBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    n = _run(kt.create_node, db, role, uid, domain_id, body.parent_id, body.title, body.summary)
    return {"id": n.id}


@router.get("/nodes/{node_id}")
def get_node(node_id: str, db: Session = Depends(get_db)):
    _require_view()
    return _run(kt.node_detail, db, node_id)


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, body: NodePatch, db: Session = Depends(get_db)):
    uid, _, role = _who()
    n = _run(kt.update_node, db, role, uid, node_id, title=body.title, summary=body.summary)
    return {"id": n.id, "version": n.version}


@router.post("/nodes/{node_id}/move")
def relocate_node(node_id: str, body: MoveBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    n = _run(kt.move_node, db, role, uid, node_id, body.parent_id)
    return {"id": n.id}


@router.delete("/nodes/{node_id}")
def remove_node(node_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return {"deleted": _run(kt.delete_node, db, role, node_id, uid)}


@router.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    _require_view()
    return {"results": kt.search_nodes(db, q)}


@router.post("/ask")
async def ask(body: AskBody) -> dict[str, Any]:
    """Run the unified tree + GraphRAG retrieval (R&D and administrators: to test the setup)."""
    from pathmind.services.knowledge_tree.unified import knowledge_search

    _, _, role = _who()
    if not kt.can_view(role):
        raise HTTPException(status_code=403, detail="Only R&D or administrators can test retrieval.")
    try:
        return await knowledge_search(body.query, domain_id=body.domain_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── R&D document store, tree export/import, audit trail ──────────────────

MAX_UPLOAD = 25 * 1024 * 1024


async def _read_limited(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File is larger than 25 MB.")
    return data


@router.get("/documents")
def list_documents(domain_id: str | None = None, db: Session = Depends(get_db)):
    _, _, role = _who()
    return {"documents": _run(kt.store.list_documents, db, role, domain_id)}


@router.post("/domains/{domain_id}/documents")
async def upload_document(domain_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    uid, _, role = _who()
    data = await _read_limited(file)
    return _run(
        kt.store.upload_document, db, role, uid,
        domain_id=domain_id, filename=file.filename or "file", data=data,
    )


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    data, ctype, name = _run(kt.store.read_document, db, role, uid, document_id)
    safe = name.replace('"', "")
    return Response(
        content=data, media_type=ctype,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    _run(kt.store.delete_document, db, role, uid, document_id)
    return {"ok": True}


@router.get("/domains/{domain_id}/export")
def export_tree(domain_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.store.export_tree, db, role, uid, domain_id)


@router.post("/domains/import-tree")
async def import_tree(file: UploadFile = File(...), name: str = Form(""), db: Session = Depends(get_db)):
    uid, _, role = _who()
    data = await _read_limited(file)
    return _run(kt.store.import_tree, db, role, uid, data, name=name.strip() or None)


# ── the graph: schema, node types, relations ───────────────────────────────


@router.get("/schema")
def graph_schema(db: Session = Depends(get_db)):
    """Node labels and relation types R&D declared (defaults seeded on first use)."""
    _, _, role = _who()
    return _run(kt.graph.schema, db, role)


class LabelBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="", max_length=16)
    description: str = Field(default="", max_length=1000)


class RelationBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=1000)


@router.post("/schema/labels")
def add_label(body: LabelBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.save_label, db, role, uid, label_id=None, **body.model_dump())


@router.patch("/schema/labels/{label_id}")
def edit_label(label_id: str, body: LabelBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.save_label, db, role, uid, label_id=label_id, **body.model_dump())


@router.delete("/schema/labels/{label_id}")
def remove_label(label_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    _run(kt.graph.delete_label, db, role, uid, label_id)
    return {"ok": True}


@router.post("/schema/relations")
def add_relation_type(body: RelationBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.save_relation, db, role, uid, relation_id=None, **body.model_dump())


@router.patch("/schema/relations/{relation_id}")
def edit_relation_type(relation_id: str, body: RelationBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.save_relation, db, role, uid, relation_id=relation_id, **body.model_dump())


@router.delete("/schema/relations/{relation_id}")
def remove_relation_type(relation_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    _run(kt.graph.delete_relation, db, role, uid, relation_id)
    return {"ok": True}


@router.get("/domains/{domain_id}/graph")
def domain_graph(domain_id: str, db: Session = Depends(get_db)):
    """All nodes and relations of a domain (outline placement as implicit PART_OF)."""
    _, _, role = _who()
    return _run(kt.graph.graph_of, db, role, domain_id)


class NodeGraphBody(BaseModel):
    label: str | None = Field(default=None, max_length=60)
    properties: dict[str, Any] | None = None
    key: str | None = Field(default=None, max_length=80)
    layer: str | None = Field(default=None, max_length=12)
    names: dict[str, str] | None = None
    description: str | None = Field(default=None, max_length=1000)
    provenance: dict[str, Any] | None = None


@router.put("/nodes/{node_id}/graph")
def put_node_graph(node_id: str, body: NodeGraphBody, db: Session = Depends(get_db)):
    """Set a node's label, properties, id, names, description, provenance (left out = unchanged)."""
    uid, _, role = _who()
    fields = body.model_dump(exclude_unset=True)
    n = _run(kt.graph.set_node_graph, db, role, uid, node_id, label=fields.get("label"),
             properties=fields.get("properties"), key=fields.get("key"), layer=fields.get("layer"),
             names=fields.get("names"), description=fields.get("description"),
             provenance=fields.get("provenance"))
    return {"id": n.id, "label": kt.graph.node_label(n), "layer": n.layer or "", "properties": n.properties or {},
            **kt.graph.entity_fields(n)}


class EdgeBody(BaseModel):
    source_id: str
    target_id: str
    type: str = Field(min_length=1, max_length=60)
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgePatch(BaseModel):
    type: str | None = Field(default=None, max_length=60)
    properties: dict[str, Any] | None = None


@router.post("/edges")
def create_edge(body: EdgeBody, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.add_edge, db, role, uid, source_id=body.source_id, target_id=body.target_id,
                rel_type=body.type, properties=body.properties)


@router.patch("/edges/{edge_id}")
def patch_edge(edge_id: str, body: EdgePatch, db: Session = Depends(get_db)):
    uid, _, role = _who()
    return _run(kt.graph.update_edge, db, role, uid, edge_id, rel_type=body.type, properties=body.properties)


@router.delete("/edges/{edge_id}")
def remove_edge(edge_id: str, db: Session = Depends(get_db)):
    uid, _, role = _who()
    _run(kt.graph.delete_edge, db, role, uid, edge_id)
    return {"ok": True}


# ── samples and the graph as a document (Co-Writer) ─────────────────────────


@router.get("/sample")
def sample_file(format: str = "md"):
    """The formatting sample as a file (md, txt, docx or pdf)."""
    _require_view()
    data, ctype, name = _run(kt.samples.sample_file, format.lower())
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/sample/markdown")
def sample_markdown():
    _require_view()
    return {"title": kt.samples.SAMPLE_TITLE, "markdown": kt.samples.SAMPLE_MARKDOWN}


@router.get("/domains/{domain_id}/markdown")
def domain_markdown(domain_id: str, db: Session = Depends(get_db)):
    """A domain as one Markdown document, to keep writing it in Co-Writer."""
    _, _, role = _who()
    return _run(kt.document_sync.export_markdown, db, role, domain_id)


class SyncBody(BaseModel):
    domain_id: str | None = None
    name: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=200)
    markdown: str = Field(min_length=1, max_length=2_000_000)


@router.post("/sync")
def sync_document(body: SyncBody, db: Session = Depends(get_db)):
    """Update a domain (or create one) from a document's headings. R&D only; revertable."""
    uid, _, role = _who()
    result = _run(
        kt.document_sync.sync_markdown, db, role, uid, domain_id=body.domain_id,
        new_domain_name=body.name.strip() or None, title=body.title, markdown=body.markdown,
    )
    audit.record(db, uid, "tree.sync", target_type="domain", target_id=result["domain_id"],
                 data={k: result[k] for k in ("created", "updated", "removed")})
    return result


# ── edit history (R&D and administrators) ─────────────────────────────────


@router.get("/history")
def tree_history(domain_id: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    """Recent edits of the main graph (all domains, or one), newest first."""
    _require_view()
    return {
        "changes": kt.history.list_changes(db, domain_id, limit),
        "deleted_domains": kt.history.deleted_domains(db),
    }


@router.get("/history/{change_id}")
def tree_change(change_id: int, db: Session = Depends(get_db)):
    _require_view()
    return _run(kt.history.change_detail, db, change_id)


@router.post("/history/{change_id}/revert")
def revert_change(change_id: int, db: Session = Depends(get_db)):
    """Undo one edit. Refused (409) while a later edit touched the same nodes."""
    uid, _, role = _who()
    return _run(kt.history.revert, db, role, uid, change_id)


@router.get("/audit")
def audit_log(action_prefix: str = "", limit: int = 100, db: Session = Depends(get_db)):
    _require_view()
    return {"entries": audit.list_recent(db, limit=max(1, min(limit, 500)), action_prefix=action_prefix)}
