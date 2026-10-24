"""Main knowledge graph: schema, typed nodes, relations, document directives, history."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.models import KnowledgeEdge, KnowledgeNode
from pathmind.services import knowledge_tree as kt

SAMPLE = kt.samples.SAMPLE_MARKDOWN


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


def _sync(db, md, domain_id=None):
    return kt.document_sync.sync_markdown(db, "rnd", "r1", domain_id=domain_id, new_domain_name="Maths",
                                          title="Maths", markdown=md)


def _node(db, did, title):
    return db.query(KnowledgeNode).filter_by(domain_id=did, title=title).one()


def _edges(db, did):
    titles = {n.id: n.title for n in db.query(KnowledgeNode).filter_by(domain_id=did).all()}
    return {(titles[e.source_id], e.rel_type, titles[e.target_id]): e.properties
            for e in db.query(KnowledgeEdge).filter_by(domain_id=did).all()}


def test_default_schema_is_seeded_and_only_rnd_changes_it(db):
    sch = kt.graph.schema(db, "admin")
    assert {"Category", "Type", "Concept"} <= {x["name"] for x in sch["labels"]}
    assert {"TYPE_OF", "PART_OF", "PREREQUISITE_OF"} <= {x["name"] for x in sch["relations"]}
    with pytest.raises(kt.TreeError):
        kt.graph.save_label(db, "admin", "a1", label_id=None, name="Product")
    row = kt.graph.save_relation(db, "rnd", "r1", relation_id=None, name="contains item")
    assert row["name"] == "CONTAINS_ITEM"
    with pytest.raises(kt.TreeError) as e:
        kt.graph.delete_relation(db, "rnd", "r1", next(x["id"] for x in kt.graph.schema(db)["relations"]
                                                       if x["name"] == "PART_OF"))
    assert e.value.status == 400


def test_sample_document_builds_a_typed_graph(db):
    did = _sync(db, SAMPLE)["domain_id"]
    assert _node(db, did, "Mathematics").label == "Category"
    assert _node(db, did, "Algebra").label == "Type"
    geo = _node(db, did, "Coordinate geometry")
    assert geo.label == "Concept" and geo.layer is None and geo.properties == {"difficulty": 2}
    edges = _edges(db, did)
    assert ("Coordinate geometry", "TYPE_OF", "Algebra") in edges
    assert ("Coordinate geometry", "TYPE_OF", "Geometry") in edges  # one node, several types
    assert edges[("Solving ax + b = 0", "EXPLAINS", "Linear equations")] == {"minutes": 20}
    graph = kt.graph.graph_of(db, "admin", did)
    implicit = [e for e in graph["edges"] if e["implicit"]]
    assert implicit and all(e["type"] == "PART_OF" for e in implicit)
    facts = kt.graph.triples(db, {geo.id})
    assert "(Coordinate geometry:Concept {difficulty: 2}) -[TYPE_OF]-> (Geometry:Type)" in facts


def test_round_trip_through_markdown_keeps_everything(db):
    did = _sync(db, SAMPLE)["domain_id"]
    before_nodes = {n.title: (n.id, n.label, n.layer, n.properties) for n in db.query(KnowledgeNode).filter_by(domain_id=did)}
    before_edges = _edges(db, did)
    md = kt.document_sync.export_markdown(db, "rnd", did)["markdown"]
    assert "@rel TYPE_OF -> #geometry" in md and "@label Category" in md and "@id linear-equations" in md
    out = _sync(db, md, did)
    assert out["created"] == 0 and out["removed"] == 0 and out["warnings"] == []
    after_nodes = {n.title: (n.id, n.label, n.layer, n.properties) for n in db.query(KnowledgeNode).filter_by(domain_id=did)}
    assert after_nodes == before_nodes
    assert _edges(db, did) == before_edges


def test_text_without_directives_keeps_graph_data_and_rel_none_clears(db):
    did = _sync(db, SAMPLE)["domain_id"]
    plain = "\n".join(line for line in SAMPLE.split("\n") if not line.startswith("@"))
    _sync(db, plain, did)
    assert _node(db, did, "Algebra").label == "Type"
    assert ("Coordinate geometry", "TYPE_OF", "Geometry") in _edges(db, did)
    md = SAMPLE.replace("@rel TYPE_OF -> Algebra\n@rel TYPE_OF -> Geometry", "@rel none")
    _sync(db, md, did)
    assert not [k for k in _edges(db, did) if k[0] == "Coordinate geometry"]


def test_bad_directives_are_warnings_not_errors(db):
    md = "# A\n@label Spaceship\n@rel FLIES_TO -> B\n@rel TYPE_OF -> Nowhere\n# B\n"
    out = _sync(db, md)
    assert len(out["warnings"]) == 3
    assert _node(db, out["domain_id"], "A").label is None


def test_edges_are_validated_recorded_and_revertable(db):
    did = _sync(db, SAMPLE)["domain_id"]
    a, b = _node(db, did, "Linear equations").id, _node(db, did, "Geometry").id
    with pytest.raises(kt.TreeError):
        kt.graph.add_edge(db, "rnd", "r1", source_id=a, target_id=a, rel_type="RELATED_TO")
    with pytest.raises(kt.TreeError):
        kt.graph.add_edge(db, "rnd", "r1", source_id=a, target_id=b, rel_type="NOT_A_TYPE")
    with pytest.raises(kt.TreeError):
        kt.graph.add_edge(db, "admin", "a1", source_id=a, target_id=b, rel_type="RELATED_TO")
    edge = kt.graph.add_edge(db, "rnd", "r1", source_id=a, target_id=b, rel_type="related to",
                             properties={"why": "lines"})
    assert edge["type"] == "RELATED_TO"
    change = kt.history.list_changes(db, did, 1)[0]
    assert change["action"] == "edge.create"
    kt.history.revert(db, "rnd", "r1", change["id"])
    assert db.get(KnowledgeEdge, edge["id"]) is None


def test_deleting_a_node_drops_its_edges_and_revert_restores_them(db):
    did = _sync(db, SAMPLE)["domain_id"]
    before = _edges(db, did)
    kt.delete_node(db, "rnd", _node(db, did, "Geometry").id, "r1")
    assert not [k for k in _edges(db, did) if "Geometry" in (k[0], k[2])]
    kt.history.revert(db, "rnd", "r1", kt.history.list_changes(db, did, 1)[0]["id"])
    assert _edges(db, did) == before


def test_label_rename_follows_nodes_and_schema_edits_revert(db):
    did = _sync(db, SAMPLE)["domain_id"]
    concept = next(x for x in kt.graph.schema(db)["labels"] if x["name"] == "Concept")
    kt.graph.save_label(db, "rnd", "r1", label_id=concept["id"], name="Idea")
    assert _node(db, did, "Linear equations").label == "Idea"
    change = kt.history.list_changes(db, kt.graph.SCHEMA_SCOPE, 1)[0]
    kt.history.revert(db, "rnd", "r1", change["id"])
    assert "Concept" in {x["name"] for x in kt.graph.schema(db)["labels"]}


def test_set_node_graph_and_json_export_import(db):
    did = _sync(db, SAMPLE)["domain_id"]
    nid = _node(db, did, "Lines through two points").id
    kt.graph.set_node_graph(db, "rnd", "r1", nid, label="Example", properties={"level": "easy"})
    n = db.get(KnowledgeNode, nid)
    assert (n.label, n.layer, n.properties) == ("Example", None, {"level": "easy"})
    with pytest.raises(kt.TreeError):
        kt.graph.set_node_graph(db, "rnd", "r1", nid, label="Spaceship")
    data = kt.store.export_tree(db, "admin", "a1", did)
    assert data["format"] == "pathmind-knowledge-graph" and data["edges"]
    out = kt.store.import_tree(db, "rnd", "r1", json.dumps(data).encode(), name="Copy")
    assert _edges(db, out["domain_id"]) == _edges(db, did)
    assert _node(db, out["domain_id"], "Lines through two points").label == "Example"


def test_entity_fields_ids_names_description_provenance(db):
    did = _sync(db, SAMPLE)["domain_id"]
    lin = _node(db, did, "Linear equations")
    assert lin.key == "linear-equations"
    assert lin.names == {"vi": "Phương trình bậc nhất"}
    assert lin.description.startswith("An equation where")
    assert lin.provenance == {"source": "Grade 8 textbook, chapter 3", "confidence": 0.95}
    assert lin.created_by == "r1" and lin.created_at is not None
    # every node gets a unique readable id, also without @id
    keys = [n.key for n in db.query(KnowledgeNode).filter_by(domain_id=did)]
    assert all(keys) and len(set(keys)) == len(keys)
    assert _node(db, did, "Coordinate geometry").key == "coordinate-geometry"
    # relations can point at an id or at a name in another language
    md = SAMPLE + "\n## Practice\n@rel USES -> #linear-equations\n@rel RELATED_TO -> Phương trình bậc nhất\n"
    out = _sync(db, md, did)
    assert out["warnings"] == []
    assert {("Practice", "USES", "Linear equations"), ("Practice", "RELATED_TO", "Linear equations")} <= set(_edges(db, did))


def test_id_keeps_the_node_when_a_heading_is_renamed(db):
    did = _sync(db, SAMPLE)["domain_id"]
    before = _node(db, did, "Linear equations").id
    _sync(db, SAMPLE.replace("## Linear equations", "## First-degree equations"), did)
    assert _node(db, did, "First-degree equations").id == before


def test_entity_edits_are_validated(db):
    did = _sync(db, SAMPLE)["domain_id"]
    nid = _node(db, did, "Algebra").id
    kt.graph.set_node_graph(db, "rnd", "r1", nid, key="algebra-1", names={"en": "Algebra", "vi": "Đại số"},
                            description="Symbols and rules.", provenance={"source": "Syllabus", "confidence": 0.8})
    n = db.get(KnowledgeNode, nid)
    assert (n.key, n.names["vi"], n.provenance["confidence"]) == ("algebra-1", "Đại số", 0.8)
    with pytest.raises(kt.TreeError) as dup:
        kt.graph.set_node_graph(db, "rnd", "r1", nid, key="linear-equations")
    assert dup.value.status == 409
    for bad in ({"key": "Has Space"}, {"names": {"english": "x"}}, {"provenance": {"confidence": 3}}):
        with pytest.raises(kt.TreeError):
            kt.graph.set_node_graph(db, "rnd", "r1", nid, **bad)


def test_layer_is_an_optional_free_grouping(db):
    md = SAMPLE.replace("## Algebra\n", "## Algebra\n\n@layer core\n")
    did = _sync(db, md)["domain_id"]
    assert _node(db, did, "Algebra").layer == "core"
    assert _node(db, did, "Geometry").layer is None
    assert "@layer core" in kt.document_sync.export_markdown(db, "rnd", did)["markdown"]
    nid = _node(db, did, "Geometry").id
    kt.graph.set_node_graph(db, "rnd", "r1", nid, layer="advanced")
    assert db.get(KnowledgeNode, nid).layer == "advanced"
    kt.graph.set_node_graph(db, "rnd", "r1", nid, layer="")
    assert db.get(KnowledgeNode, nid).layer is None
    with pytest.raises(kt.TreeError):
        kt.graph.set_node_graph(db, "rnd", "r1", nid, layer="far too long a layer")
    assert "layers" not in kt.graph.schema(db) and "layer" not in kt.graph.schema(db)["labels"][0]
