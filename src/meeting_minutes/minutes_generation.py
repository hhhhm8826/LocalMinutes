"""Utterance-only model input and server materialization of result documents."""
from datetime import datetime, timezone
import re

from pydantic import ValidationError

from .codex_provider import CodexFailure
from .documents import (DocumentMetadata, GeneratedMeeting, ManualAction, MeetingDocument,
                        Topic, resolve_starts, normalize_meeting_topics, GeneratedVideo, VideoClaim, VideoDocument)
from .minutes_validation import text_payload, validate_minutes
from .utterances import RULE_VERSION, derive_utterances

PROMPT_VERSION = 'topic-minutes-5'
VIDEO_PROMPT_VERSION = 'video-summary-3'


def generation_model(meeting):
    return GeneratedVideo if meeting.get('document_kind') == 'video_summary' else GeneratedMeeting


def prompt_version(meeting):
    return VIDEO_PROMPT_VERSION if meeting.get('document_kind') == 'video_summary' else PROMPT_VERSION


def generation_payload(meeting_id, version, revision, meeting, transcript):
    # Source/document metadata belongs outside the strict upload settings contract.
    options = {key: value for key, value in meeting.items() if key not in {'document_kind', 'source_kind', 'source_metadata'}}
    payload = text_payload(meeting_id, version, revision, options, transcript)
    try:
        reading = derive_utterances(transcript, version)
    except ValueError as exc:
        raise CodexFailure(str(exc)) from exc
    payload.update(document_kind=meeting.get('document_kind', 'meeting'), utterance_rule=RULE_VERSION,
                   prompt_version=prompt_version(meeting),
                   segments=[dict(u, overlap='overlap' in u['uncertainty']) for u in reading['utterances']])
    if payload['document_kind'] == 'video_summary':
        metadata = meeting.get('source_metadata', {})
        payload['source'] = {key: metadata.get(key) for key in ('title', 'channel', 'published_at')}
        payload['meeting']['occurred_at'] = None
    return payload


def validate_generation(value, payload, meeting):
    try:
        result = generation_model(payload).model_validate(value)
        reading = {'utterances': payload['segments']}
        for topic in result.topics:
            resolve_starts(topic.ranges, reading)
    except (ValueError, ValidationError) as exc:
        raise CodexFailure('MINUTES_SCHEMA_INVALID') from exc
    if isinstance(result, GeneratedVideo):
        if any(getattr(result, key) != payload[key] for key in ('meeting_id', 'transcript_version', 'revision')):
            raise CodexFailure('MINUTES_SNAPSHOT_MISMATCH')
        if len({topic.id for topic in result.topics}) != len(result.topics):
            raise CodexFailure('MINUTES_DUPLICATE_IDS')
        sources = {segment['id'] for segment in payload['segments']}
        if any(not set(claim.source_segment_ids) <= sources or not claim.attribution.strip() for claim in result.claims):
            raise CodexFailure('MINUTES_EVIDENCE_REQUIRED')
        if not re.search('[가-힣]', result.summary):
            raise CodexFailure('MINUTES_KOREAN_REQUIRED')
        return result
    legacy_validation = result.model_dump(exclude={'document_kind', 'schema_version', 'topics', 'title'})
    # Topic ranges may span more than the old 100-word evidence-list limit.
    # Their complete ranges were checked above; retain that full coverage.
    legacy_validation.update(schema_version=1, topics=[])
    validate_minutes(legacy_validation, payload, {key: value for key, value in meeting.items()
                     if key not in {'document_kind', 'source_kind', 'source_metadata'}})
    all_items = [*result.topics, *result.decisions, *result.action_items, *result.open_questions]
    if len({item.id for item in all_items}) != len(all_items):
        raise CodexFailure('MINUTES_DUPLICATE_IDS')
    return result


def materialize_generation(value, payload, meeting):
    result = validate_generation(value, payload, meeting)
    reading = {'utterances': payload['segments']}
    title = (meeting.get('source_metadata', {}).get('title') if meeting.get('source_kind') == 'youtube' and not meeting.get('title') else None) or meeting.get('title') or result.title
    if not title or not title.strip():
        raise CodexFailure('DOCUMENT_TITLE_REQUIRED')
    topics = []
    for topic in result.topics:
        starts = resolve_starts(topic.ranges, reading)
        topics.append(Topic(**topic.model_dump(exclude={'ranges'}), starts=starts,
                            source_segment_ids=list(dict.fromkeys(i for start in starts for i in start.utterance_ids))))
    if isinstance(result, GeneratedVideo):
        source = meeting.get('source_metadata', {})
        return VideoDocument(meeting_id=result.meeting_id, transcript_version=result.transcript_version,
            revision=result.revision, summary=result.summary, topics=topics,
            claims=[VideoClaim(**claim.model_dump(exclude={'source_segment_ids'})) for claim in result.claims],
            metadata=DocumentMetadata(title=title, generated_at=datetime.now(timezone.utc),
                source_kind=meeting.get('source_kind', 'file'), source_url=source.get('source_url'),
                channel=source.get('channel'), published_at=source.get('published_at'),
                prompt_version=VIDEO_PROMPT_VERSION, utterance_rule=payload['utterance_rule']))
    names = {s['id']: s['name'] for s in payload['speakers']}
    actions = [ManualAction(**item.model_dump(), owner_name=names.get(item.owner_speaker_id)) for item in result.action_items]
    return normalize_meeting_topics(MeetingDocument(meeting_id=result.meeting_id, transcript_version=result.transcript_version,
        revision=result.revision, status=result.status, summary=result.summary, topics=topics,
        decisions=result.decisions, action_items=actions, open_questions=result.open_questions,
        review_notes=result.review_notes, metadata=DocumentMetadata(
            title=title, occurred_at=payload['meeting']['occurred_at'],
            timezone=payload['meeting']['timezone'], generated_at=datetime.now(timezone.utc),
            prompt_version=PROMPT_VERSION, utterance_rule=payload.get('utterance_rule', RULE_VERSION))))
