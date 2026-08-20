"""Minimized study-identity and independence fixtures for F5."""
from __future__ import annotations

from .f5_study_cluster import (
    StudyIdentity, cluster_studies, compare_studies, identity_from_mapping,
    source_bound_distinct_data,
)
from . import judgment_run as jr


def test_shared_registry_number_is_same_study_despite_different_authors():
    cited = identity_from_mapping(
        {"registry_ids": ["NCT-00000001"]}, work_id="111")
    candidate = identity_from_mapping(
        {"registry_ids": ["nct00000001"]}, work_id="222")
    relation = compare_studies(cited, candidate)
    assert relation.independence == "not_independent"
    assert relation.basis == "shared_registry_identifier"
    assert relation.cited_cluster_id == relation.candidate_cluster_id


def test_same_pmid_is_not_independent_even_without_other_identifiers():
    relation = compare_studies(StudyIdentity("111"), StudyIdentity("111"))
    assert relation.independence == "not_independent"
    assert relation.basis == "same_work_id"


def test_same_cohort_with_different_authors_is_not_independent():
    relation = compare_studies(
        StudyIdentity("111", cohort_ids=("cohort alpha",)),
        StudyIdentity("222", cohort_ids=("cohort alpha",)),
    )
    assert relation.independence == "not_independent"
    assert relation.basis == "shared_cohort_identity"


def test_doi_or_explicit_version_relationship_clusters_versions():
    same_doi = compare_studies(
        StudyIdentity("111", doi="10.1234/example"),
        StudyIdentity("222", doi="10.1234/example"),
    )
    linked = compare_studies(
        StudyIdentity("111", version_work_ids=("222",)),
        StudyIdentity("222"),
    )
    assert same_doi.independence == linked.independence == "not_independent"
    assert same_doi.basis == linked.basis == "established_version_relationship"


def test_different_pmids_titles_and_authors_do_not_invent_independence():
    relation = compare_studies(StudyIdentity("111"), StudyIdentity("222"))
    assert relation.independence == "unknown"
    assert relation.cluster_uncertain is True
    assert relation.cited_cluster_id != relation.candidate_cluster_id


def test_distinct_registry_ids_alone_do_not_prove_independence():
    relation = compare_studies(
        StudyIdentity("111", registry_ids=("nct001",), primary_study=True),
        StudyIdentity("222", registry_ids=("nct002",), primary_study=True),
    )
    assert relation.independence == "unknown"
    assert relation.basis == "cluster_identity_insufficient"


def test_same_authors_with_explicitly_distinct_data_can_be_independent():
    relation = compare_studies(
        StudyIdentity("111", demonstrably_distinct_from=("222",)),
        StudyIdentity("222"),
    )
    assert relation.independence == "independent"
    assert relation.basis == "explicit_distinct_data"


def test_meta_analysis_registration_is_not_naively_an_independent_trial():
    relation = compare_studies(
        StudyIdentity("111", registry_ids=("nct001",), primary_study=True),
        StudyIdentity("222", registry_ids=("prospero001",), primary_study=False),
    )
    assert relation.independence == "unknown"
    assert relation.basis == "cluster_identity_insufficient"


def test_ten_reports_from_one_registry_count_as_one_cluster():
    studies = tuple(
        StudyIdentity(str(100 + index), registry_ids=("nct001",))
        for index in range(10)
    )
    clusters = cluster_studies(studies)
    assert len(clusters) == 1
    assert len(clusters[0].work_ids) == 10
    assert clusters[0].cluster_uncertain is False


def test_transitive_version_chain_is_one_cluster():
    clusters = cluster_studies((
        StudyIdentity("111", version_work_ids=("222",)),
        StudyIdentity("222", version_work_ids=("333",)),
        StudyIdentity("333"),
    ))
    assert len(clusters) == 1
    assert clusters[0].work_ids == ("111", "222", "333")


def test_unknown_papers_remain_separate_uncertain_fallback_clusters():
    clusters = cluster_studies((StudyIdentity("111"), StudyIdentity("222")))
    assert len(clusters) == 2
    assert all(cluster.cluster_uncertain for cluster in clusters)


def test_shared_unretained_version_parent_still_forms_one_family():
    clusters = cluster_studies((
        StudyIdentity("111", version_work_ids=("222",)),
        StudyIdentity("333", version_work_ids=("222",)),
    ))
    assert len(clusters) == 1
    assert clusters[0].basis == "established_version_relationship"


def test_meta_analysis_cannot_bridge_two_primary_trials_into_one_vote():
    clusters = cluster_studies((
        StudyIdentity(
            "111", registry_ids=("nct00000001",), primary_study=True),
        StudyIdentity(
            "333", registry_ids=("nct00000002",), primary_study=True),
        StudyIdentity(
            "222", registry_ids=("nct00000001", "nct00000002"),
            primary_study=False),
    ))
    assert len(clusters) == 3


def test_placeholder_identifiers_cannot_merge_unrelated_studies():
    cited = identity_from_mapping({
        "doi": "unknown",
        "registry_ids": ["clinicaltrialsgov:not available"],
    }, work_id="111")
    candidate = identity_from_mapping({
        "doi": "unknown",
        "registry_ids": ["clinicaltrialsgov:not available"],
    }, work_id="222")
    assert compare_studies(cited, candidate).independence == "unknown"


def test_major_registry_identifiers_preserve_shared_study_edges():
    identifiers = (
        "CTRI/2020/01/023456", "DRKS00012345",
        "IRCT20200101000001N1", "PACTR202001234567890",
    )
    for identifier in identifiers:
        relation = compare_studies(
            identity_from_mapping({"registry_ids": [identifier]}, work_id="111"),
            identity_from_mapping({"registry_ids": [identifier]}, work_id="222"),
        )
        assert relation.independence == "not_independent", identifier


def test_namespaced_and_raw_registry_forms_are_the_same_trial():
    relation = compare_studies(
        identity_from_mapping(
            {"registry_ids": ["NCT00000001"]}, work_id="111"),
        identity_from_mapping(
            {"registry_ids": ["clinicaltrialsgov:NCT00000001"]},
            work_id="222"),
    )
    assert relation.independence == "not_independent"


def test_valid_opaque_doi_suffix_preserves_version_identity():
    doi = "10.1002/(SICI)1099-0844(199912)17:4<290::AID-CBF849>3.0.CO;2-P"
    relation = compare_studies(
        identity_from_mapping({"doi": doi}, work_id="111"),
        identity_from_mapping({"doi": doi}, work_id="222"),
    )
    assert relation.independence == "not_independent"


def test_source_bound_distinct_rejects_cross_sentence_subgroup_conflict():
    cited = "Cohort Alpha, Chicago, 2009. Drug X reduced disease Y risk."
    candidate = (
        "Cohort Beta was a subgroup of Cohort Alpha. "
        "No participants came from Cohort Alpha. "
        "Drug X did NOT reduce disease Y.")
    assert source_bound_distinct_data(cited, candidate) == (False, None)


def test_manifest_merges_version_components_across_claim_candidate_subsets():
    records = [
        {"study_clusters": [{
            "cluster_id": "local-a", "work_ids": ["111"],
            "identity_evidence_ids": ["pmid:111", "pmid:222"],
        }], "candidate_assessments": []},
        {"study_clusters": [{
            "cluster_id": "local-c", "work_ids": ["333"],
            "identity_evidence_ids": ["pmid:333", "pmid:222"],
        }], "candidate_assessments": []},
    ]
    block = jr._f5_manifest_block(
        None, records, {"retrieval_calls": 0, "attestation_calls": 0,
                        "judge_calls": 0, "retrieval_protocols": []})
    assert block["study_cluster_count"] == 1
    assert block["study_clusters"][0]["identity_evidence_ids"] == [
        "pmid:111", "pmid:222", "pmid:333"]


def test_manifest_does_not_bridge_trials_through_nonprimary_registry_context():
    clusters = cluster_studies((
        StudyIdentity(
            "111", registry_ids=("nct00000001",), primary_study=True),
        StudyIdentity(
            "333", registry_ids=("nct00000002",), primary_study=True),
        StudyIdentity(
            "222", registry_ids=("nct00000001", "nct00000002"),
            primary_study=False),
    ))
    records = [{"study_clusters": [{
        "cluster_id": cluster.cluster_id,
        "work_ids": list(cluster.work_ids),
        "identity_evidence_ids": list(cluster.identity_evidence_ids),
    } for cluster in clusters], "candidate_assessments": []}]
    block = jr._f5_manifest_block(
        None, records, {"retrieval_calls": 0, "attestation_calls": 0,
                        "judge_calls": 0, "retrieval_protocols": []})
    assert block["study_cluster_count"] == 3
