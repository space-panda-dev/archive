"""Names of the source record schemas covered by the V2 migration census.

These labels define the census vocabulary only. No label implies an Archive
contract or an accepted migration route; all remain unresolved until Archive
authority establishes one.
"""

SOURCE_SCHEMAS: tuple[str, ...] = (
    "artifact", "assessment", "attempt", "audio_feature_timeline",
    "audio_label", "audio_musical_analysis", "audio_segment",
    "audio_technical_metadata", "audio_transcript",
    "audio_transcript_model_preset", "capture_view", "claim",
    "coverage_observation", "derived_artifact", "document_binding",
    "document_evaluation", "dynamic_document", "entity", "event",
    "gcal_artifact", "gcal_ingest_batch", "gcal_metadata", "gcal_source",
    "github_artifact", "github_ingest_batch", "github_metadata",
    "github_source", "import_batch", "job", "org_artifact", "org_document",
    "org_ingest_batch", "org_metadata", "org_source", "policy_decision",
    "provenance", "published_artifact", "record_revision", "seedbox_artifact",
    "seedbox_ingest_batch", "seedbox_metadata", "seedbox_source",
    "segment_view", "session", "song_performance", "source_observation",
)

if len(SOURCE_SCHEMAS) != 46 or len(set(SOURCE_SCHEMAS)) != len(SOURCE_SCHEMAS):
    raise RuntimeError("source schema catalog must contain 46 unique labels")

UNRESOLVED_REASON = (
    "no Archive-authorized migration route is defined for this source schema"
)
