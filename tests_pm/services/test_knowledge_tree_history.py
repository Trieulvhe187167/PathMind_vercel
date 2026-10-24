"""Main knowledge tree: every staff edit is recorded and can be reverted."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.models import KnowledgeNode, KnowledgeSource
from pathmind.services import knowledge_tree as kt

DOC = b"# Algebra\nIntro.\n## Linear equations\nSolve ax+b=0.\n## Quadratics\nFormula.\n"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    import pathmind.database.connection as connection
    from pathmind.services.storage import object_store

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(connection, "SessionLocal", factory)
    monkeypatch.setattr(object_store, "_implicit_root", lambda: str(tmp_path / "objects"))
    return factory()


def _ids(db, domain_id):
    return {n.title: n.id for n in db.query(KnowledgeNode).filter_by(domain_id=domain_id).all()}


def _latest(db, domain_id=None):
    return kt.history.list_changes(db, domain_id, 1)[0]


def test_rnd_edits_are_recorded_and_revertable(db):
    out = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="Maths", filename="m.md", data=DOC)
    did = out["domain_id"]
    first = _latest(db, did)
    assert first["action"] == "import" and first["actor_id"] == "r1"
    lin = _ids(db, did)["Linear equations"]

    kt.update_node(db, "rnd", "r1", lin, title="Linear eq.")
    change = _latest(db, did)
    detail = kt.history.change_detail(db, change["id"])
    assert detail["items"][0]["before"] == "Linear equations" and "title" in detail["items"][0]["fields"]

    kt.history.revert(db, "rnd", "r1", change["id"])
    assert db.get(KnowledgeNode, lin).title == "Linear equations"
    with pytest.raises(kt.TreeError) as twice:
        kt.history.revert(db, "rnd", "r1", change["id"])
    assert twice.value.status == 409


def test_newer_edit_blocks_revert_until_it_is_reverted(db):
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="M", filename="m.md", data=DOC)["domain_id"]
    lin = _ids(db, did)["Linear equations"]
    kt.update_node(db, "rnd", "r1", lin, title="v2")
    older = _latest(db, did)["id"]
    kt.update_node(db, "rnd", "r2", lin, title="v3")
    newer = _latest(db, did)["id"]
    with pytest.raises(kt.TreeError) as blocked:
        kt.history.revert(db, "rnd", "r1", older)
    assert blocked.value.status == 409 and f"#{newer}" in blocked.value.message
    kt.history.revert(db, "rnd", "r1", newer)
    kt.history.revert(db, "rnd", "r1", older)
    assert db.get(KnowledgeNode, lin).title == "Linear equations"


def test_deleted_subtree_and_domain_come_back(db):
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="M", filename="m.md", data=DOC)["domain_id"]
    before = _ids(db, did)
    kt.delete_node(db, "rnd", before["Algebra"], "r1")
    assert _ids(db, did) == {}
    kt.history.revert(db, "rnd", "r1", _latest(db, did)["id"])
    assert _ids(db, did) == before

    kt.delete_domain(db, "rnd", did, "r1")
    assert kt.history.deleted_domains(db)[0]["domain_id"] == did
    kt.history.revert(db, "rnd", "r1", _latest(db, did)["id"])
    assert _ids(db, did) == before
    src = db.query(KnowledgeSource).filter_by(domain_id=did).one()
    assert "Solve ax+b=0." in src.text
    assert kt.history.deleted_domains(db) == []


def test_a_revert_can_itself_be_reverted(db):
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="M", filename="m.md", data=DOC)["domain_id"]
    quad = _ids(db, did)["Quadratics"]
    kt.update_node(db, "rnd", "r1", quad, summary="new summary")
    edit = _latest(db, did)["id"]
    undo = kt.history.revert(db, "rnd", "r1", edit)["change"]["id"]
    assert db.get(KnowledgeNode, quad).summary == "Formula."
    kt.history.revert(db, "rnd", "r1", undo)  # redo
    assert db.get(KnowledgeNode, quad).summary == "new summary"
    # the original edit is open again and can be reverted once more
    kt.history.revert(db, "rnd", "r1", edit)
    assert db.get(KnowledgeNode, quad).summary == "Formula."


def test_only_staff_can_revert(db):
    did = kt.create_domain(db, "rnd", "r1", "X").id
    change = _latest(db, did)["id"]
    for role in ("user", "educator", "admin"):
        with pytest.raises(kt.TreeError) as e:
            kt.history.revert(db, role, "u", change)
        assert e.value.status == 403


# ── R&D only edits; administrators read ─────────────────────────────────────


def test_admin_reads_but_cannot_edit_or_revert(db):
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="M", filename="m.md", data=DOC)["domain_id"]
    assert kt.can_view("admin") and not kt.can_edit("admin")
    with pytest.raises(kt.TreeError) as e:
        kt.create_node(db, "admin", "a1", did, None, "x")
    assert e.value.status == 403
    with pytest.raises(kt.TreeError):
        kt.history.revert(db, "admin", "a1", _latest(db, did)["id"])
    assert kt.document_sync.export_markdown(db, "admin", did)["markdown"].startswith("# Algebra")
    assert kt.store.list_documents(db, "admin", did)


# ── the tree as a document (Co-Writer) ─────────────────────────────────────


def test_sync_keeps_ids_adds_and_removes_and_can_be_reverted(db):
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="M", filename="m.md", data=DOC)["domain_id"]
    before = _ids(db, did)
    md = kt.document_sync.export_markdown(db, "rnd", did)["markdown"]
    assert "## Linear equations\n\n@id linear-equations\n\nSolve ax+b=0." in md
    edited = md.replace("## Quadratics\n\n@id quadratics\n\nFormula.", "## Inequalities\n\nSigns flip.")
    edited += "\n# Geometry\n\nShapes.\n"
    out = kt.document_sync.sync_markdown(db, "rnd", "r1", domain_id=did, new_domain_name=None,
                                         title="Maths notes", markdown=edited)
    assert out["created"] == 2 and out["removed"] == 1
    after = _ids(db, did)
    assert after["Algebra"] == before["Algebra"] and after["Linear equations"] == before["Linear equations"]
    assert "Quadratics" not in after and {"Inequalities", "Geometry"} <= set(after)
    assert db.get(KnowledgeNode, after["Inequalities"]).summary == "Signs flip."
    change = _latest(db, did)
    assert change["action"] == "document.sync"
    kt.history.revert(db, "rnd", "r1", change["id"])
    assert _ids(db, did) == before


def test_sync_creates_a_domain_and_admin_cannot_sync(db):
    out = kt.document_sync.sync_markdown(db, "rnd", "r1", domain_id=None, new_domain_name="Sample",
                                         title="Sample", markdown=kt.samples.SAMPLE_MARKDOWN)
    assert out["created"] == 8 and out["removed"] == 0
    with pytest.raises(kt.TreeError) as e:
        kt.document_sync.sync_markdown(db, "admin", "a1", domain_id=out["domain_id"], new_domain_name=None,
                                       title="x", markdown="# x")
    assert e.value.status == 403


@pytest.mark.parametrize("fmt", ["md", "txt", "docx", "pdf"])
def test_every_sample_format_imports_to_the_same_outline(db, fmt):
    data, _, name = kt.samples.sample_file(fmt)
    did = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name=fmt, filename=name, data=data)["domain_id"]
    assert len(_ids(db, did)) == 8
