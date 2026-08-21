"""Disk-backed F7 authority parity and fail-closed boundary tests."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from .f7_entity import F7Authority
from .f7_seams import (
    AuthoritySnapshotSource,
    AuthoritySQLiteIndexSource,
    FrozenAuthorityNormalizer,
    FrozenSQLiteAuthorityNormalizer,
    build_frozen_sqlite_authority_index,
)


AUTH = {"gene": "HGNC", "variant": "ClinVar", "drug": "RxNorm",
        "disease": "MONDO"}


def alias(surface, *, approved=True, through=None):
    return {
        "surface": surface, "source_db": "authority",
        "mapping_method": "approved_synonym", "approved": approved,
        "valid_from": "2025-01-01", "valid_through": through,
    }


def record(entity_id, label, aliases=(), *, through=None):
    return {
        "id": entity_id, "canonical_label": label, "status": "active",
        "valid_from": "2025-01-01", "valid_through": through,
        "aliases": list(aliases),
    }


def fixtures(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    records = {
        "gene": [
            record("HGNC:1", "GENE1", [alias("shared"), alias("old", through="2025-06-01")]),
            record("HGNC:2", "GENE2", [alias("shared")]),
        ],
        "variant": [record("ClinVar:1", "v1"), record("ClinVar:2", "v2")],
        "drug": [record("RxNorm:1", "Drug A"), record("RxNorm:2", "Drug B")],
        "disease": [record("MONDO:1", "Disease A"), record("MONDO:2", "Disease B")],
    }
    relations = {
        "gene": [], "variant": [],
        "drug": [{"left_id": "RxNorm:1", "right_id": "RxNorm:2",
                  "relation": "claim_subsumes_evidence"}],
        "disease": [],
    }
    sources = []
    for entity_type, authority in AUTH.items():
        payload = {
            "schema_version": "f7_authority_snapshot_v1",
            "entity_type": entity_type, "authority": authority,
            "version": "2026-08", "release_date": "2026-08-01",
            "records": records[entity_type], "relations": relations[entity_type],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        path = tmp_path / f"{entity_type}.json"
        path.write_bytes(raw)
        sources.append(AuthoritySnapshotSource(
            entity_type, authority, "2026-08", "2026-08-20", str(path),
            hashlib.sha256(raw).hexdigest(), True))
    return sources


def build_pair(tmp_path):
    sources = fixtures(tmp_path)
    memory = FrozenAuthorityNormalizer(sources)
    indexes = [build_frozen_sqlite_authority_index(
        source, tmp_path / f"{source.entity_type}.sqlite",
        scratch_dir=tmp_path / "scratch") for source in sources]
    disk = FrozenSQLiteAuthorityNormalizer(sources, indexes)
    return sources, indexes, memory, disk


def lock(entity_type):
    return F7Authority(AUTH[entity_type], "2026-08", "2026-08-20", True)


def test_disk_backed_normalizer_has_exact_semantic_parity(tmp_path):
    _, _, memory, disk = build_pair(tmp_path)
    assert isinstance(disk, FrozenAuthorityNormalizer)
    assert disk.authorities_json() == memory.authorities_json()
    assert disk.source_manifest() == memory.source_manifest()
    for entity_type, surfaces in {
        "gene": ["GENE1", "shared", "old", "missing", ""],
        "variant": ["v1", "ClinVar:2", "missing"],
        "drug": ["Drug A", "RxNorm:2", "missing"],
        "disease": ["Disease A", "MONDO:2", "missing"],
    }.items():
        for surface in surfaces:
            assert disk.normalize(entity_type, surface, lock=lock(entity_type)) == \
                memory.normalize(entity_type, surface, lock=lock(entity_type))
    for entity_type, left, right in [
        ("gene", "HGNC:1", "HGNC:2"),
        ("variant", "ClinVar:1", "ClinVar:2"),
        ("drug", "RxNorm:1", "RxNorm:2"),
        ("drug", "RxNorm:2", "RxNorm:1"),
        ("disease", "MONDO:1", "MONDO:2"),
        ("gene", "HGNC:1", "missing"),
    ]:
        assert disk.compare(left, right, entity_type, lock=lock(entity_type)) == \
            memory.compare(left, right, entity_type, lock=lock(entity_type))


def test_build_and_load_never_call_read_bytes(tmp_path, monkeypatch):
    sources = fixtures(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("whole-file read_bytes is forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    indexes = [build_frozen_sqlite_authority_index(
        source, tmp_path / f"{source.entity_type}.sqlite") for source in sources]
    disk = FrozenSQLiteAuthorityNormalizer(sources, indexes)
    assert disk.normalize("gene", "GENE1", lock=lock("gene"))["id"] == "HGNC:1"


def test_indexes_are_opened_query_only(tmp_path):
    _, _, _, disk = build_pair(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        disk._connection("gene").execute("DELETE FROM record")


def test_source_and_index_hashes_are_both_enforced(tmp_path):
    sources, indexes, _, _ = build_pair(tmp_path)
    index_path = Path(indexes[0].path)
    index_path.write_bytes(index_path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="SQLite index sha256 mismatch"):
        FrozenSQLiteAuthorityNormalizer(sources, indexes)

    sources = fixtures(tmp_path / "fresh")
    indexes = [build_frozen_sqlite_authority_index(
        source, tmp_path / "fresh" / f"{source.entity_type}.sqlite")
        for source in sources]
    source_path = Path(sources[0].path)
    source_path.write_bytes(source_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="snapshot sha256 mismatch"):
        FrozenSQLiteAuthorityNormalizer(sources, indexes)


def test_streaming_builder_rejects_duplicate_nested_json_keys(tmp_path):
    raw = (
        b'{"schema_version":"f7_authority_snapshot_v1","entity_type":"gene",'
        b'"authority":"HGNC","version":"2026-08","release_date":"2026-08-01",'
        b'"records":[{"id":"HGNC:1","canonical_label":"G","status":"active",'
        b'"valid_from":"2025-01-01","valid_through":null,"aliases":[{'
        b'"surface":"A","surface":"B","source_db":"HGNC",'
        b'"mapping_method":"alias","approved":true,"valid_from":"2025-01-01",'
        b'"valid_through":null}]}],"relations":[]}'
    )
    path = tmp_path / "duplicate.json"
    path.write_bytes(raw)
    source = AuthoritySnapshotSource(
        "gene", "HGNC", "2026-08", "2026-08-20", str(path),
        hashlib.sha256(raw).hexdigest(), True)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        build_frozen_sqlite_authority_index(source, tmp_path / "duplicate.sqlite")


def test_index_lock_cannot_be_relabelled_for_another_entity_type(tmp_path):
    sources, indexes, _, _ = build_pair(tmp_path)
    forged = [AuthoritySQLiteIndexSource(
        "variant" if row.entity_type == "gene" else row.entity_type,
        row.path, row.sha256) for row in indexes]
    with pytest.raises(ValueError, match="exactly one pinned"):
        FrozenSQLiteAuthorityNormalizer(sources, forged)
