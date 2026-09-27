"""Allowlisted outbound text and local structural/evidence validation."""
from datetime import date, timedelta
import re
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .contracts import MeetingCreate, Minutes, Segment
from .codex_provider import CodexFailure


def text_payload(meeting_id, transcript_version, revision, meeting, transcript, *, require_consent=True):
    settings = MeetingCreate.model_validate(meeting)
    if require_consent and not settings.allow_external_text:
        raise CodexFailure('EXTERNAL_TEXT_NOT_ALLOWED')
    segments = [Segment.model_validate(segment) for segment in transcript['segments']]
    speakers = transcript['speakers']
    return {'meeting_id': meeting_id, 'transcript_version': transcript_version, 'revision': revision,
            'meeting': {'title': settings.title, 'occurred_at': settings.occurred_at.isoformat() if settings.occurred_at else None,
                        'timezone': settings.timezone},
            'speakers': [{'id': speaker_id, 'name': value['name']} for speaker_id, value in speakers.items()],
            'segments': [{'id': segment.id, 'start_ms': segment.start_ms, 'end_ms': segment.end_ms,
                          'speaker_id': segment.speaker_id, 'text': segment.text,
                          'needs_review': segment.needs_review, 'overlap': segment.overlap} for segment in segments]}


def supported_date(expression, meeting):
    expression = expression.strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', expression):
        try:
            return date.fromisoformat(expression).isoformat()
        except ValueError:
            return None
    absolute = re.fullmatch(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', expression)
    if absolute:
        try:
            return date(*map(int, absolute.groups())).isoformat()
        except ValueError:
            return None
    offsets = {'오늘': 0, '내일': 1, '모레': 2, 'today': 0, 'tomorrow': 1, 'the day after tomorrow': 2}
    offset = offsets.get(expression.casefold())
    if offset is None or not meeting.occurred_at:
        return None
    return (meeting.occurred_at.astimezone(ZoneInfo(meeting.timezone)).date() + timedelta(days=offset)).isoformat()


def validate_minutes(value, payload, meeting, *, generated=True):
    try:
        result = Minutes.model_validate(value)
    except ValidationError as exc:
        raise CodexFailure('MINUTES_SCHEMA_INVALID') from exc
    if (result.meeting_id != payload['meeting_id'] or result.transcript_version != payload['transcript_version']
            or result.revision != payload['revision'] or (generated and result.status != 'draft')):
        raise CodexFailure('MINUTES_SNAPSHOT_MISMATCH')
    segments = {segment['id']: segment for segment in payload['segments']}
    speakers = {speaker['id'] for speaker in payload['speakers']}
    if len(segments) != len(payload['segments']):
        raise CodexFailure('TRANSCRIPT_DUPLICATE_IDS')
    all_items = [*result.topics, *result.decisions, *result.action_items, *result.open_questions]
    if len({item.id for item in all_items}) != len(all_items):
        raise CodexFailure('MINUTES_DUPLICATE_IDS')
    for item in all_items:
        if generated and item.review_status != 'needs_review':
            raise CodexFailure('MINUTES_UNVERIFIED_STATUS')
        if item.review_status == 'user_authored' and item.source_segment_ids:
            raise CodexFailure('MINUTES_USER_AUTHORED_EVIDENCE')
        if len(set(item.source_segment_ids)) != len(item.source_segment_ids):
            raise CodexFailure('MINUTES_EVIDENCE_INVALID')
        if any(source not in segments or not segments[source]['text'].strip() for source in item.source_segment_ids):
            raise CodexFailure('MINUTES_EVIDENCE_INVALID')
    for item in [*result.decisions, *result.action_items]:
        if item.review_status != 'user_authored' and not item.source_segment_ids:
            raise CodexFailure('MINUTES_EVIDENCE_REQUIRED')
    for item in result.action_items:
        if item.owner_speaker_id is not None and item.owner_speaker_id not in speakers:
            raise CodexFailure('MINUTES_OWNER_INVALID')
        if (generated or item.review_status != 'user_authored') and item.due_date_original_expression is not None:
            source_text = ' '.join(segments[source]['text'] for source in item.source_segment_ids)
            if item.due_date_original_expression.casefold() not in source_text.casefold():
                raise CodexFailure('MINUTES_DATE_UNSUPPORTED')
        if item.due_date is not None:
            if not generated and item.review_status == 'user_authored':
                try:
                    if date.fromisoformat(item.due_date).isoformat() != item.due_date:
                        raise ValueError('ISO date required')
                except ValueError as exc:
                    raise CodexFailure('MINUTES_DATE_UNSUPPORTED') from exc
                continue
            if not item.due_date_original_expression:
                raise CodexFailure('MINUTES_DATE_UNSUPPORTED')
            if supported_date(item.due_date_original_expression, MeetingCreate.model_validate(meeting)) != item.due_date:
                raise CodexFailure('MINUTES_DATE_UNSUPPORTED')
    if not re.search('[가-힣]', result.summary):
        raise CodexFailure('MINUTES_KOREAN_REQUIRED')
    return result
