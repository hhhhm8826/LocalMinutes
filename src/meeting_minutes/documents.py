"""Self-contained result documents and source-derived topic positions."""
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import ActionItem, Contract, EvidenceItem, Minutes
from .utterances import RULE_VERSION, derive_utterances


class UtteranceRange(Contract):
    first: str = Field(min_length=1, max_length=80)
    last: str = Field(min_length=1, max_length=80)


class TopicStart(Contract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    utterance_ids: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms < self.start_ms:
            raise ValueError('INVALID_TOPIC_TIME')
        return self


class GeneratedTopic(Contract):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=60, description="2-6 word Korean noun phrase naming the subject, not a sentence excerpt; e.g. 서버 예산 조정")
    text: str = Field(min_length=1, max_length=10000)
    conclusion: str = Field(max_length=10000)
    conclusion_kind: Literal['agreement', 'proposal', 'deferred', 'discussion']
    ranges: list[UtteranceRange] = Field(min_length=1, max_length=100)


    @field_validator('title')
    @classmethod
    def concise_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('TOPIC_TITLE_REQUIRED')
        return value


class Topic(Contract):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(max_length=300)
    text: str = Field(max_length=10000)
    conclusion: str = Field(default='', max_length=10000)
    conclusion_kind: Literal['agreement', 'proposal', 'deferred', 'discussion'] = 'discussion'
    starts: list[TopicStart] = Field(default_factory=list, max_length=100)
    source_segment_ids: list[str] = Field(default_factory=list)
    origin: Literal['generated', 'user_authored', 'legacy'] = 'generated'


class DocumentMetadata(Contract):
    title: str = Field(max_length=200)
    occurred_at: str | None = None
    timezone: str = 'Asia/Seoul'
    generated_at: datetime | None = None
    source_kind: Literal['file', 'youtube'] = 'file'
    source_url: str | None = None
    channel: str | None = None
    published_at: str | None = None
    prompt_version: str | None = None
    utterance_rule: str | None = None


class ManualAction(ActionItem):
    owner_name: str | None = Field(default=None, max_length=200)


class ResultBase(Contract):
    schema_version: Literal[2] = 2
    meeting_id: str
    transcript_version: str | None
    revision: int = Field(ge=1)
    language: Literal['ko'] = 'ko'
    metadata: DocumentMetadata
    summary: str = Field(max_length=20000)
    topics: list[Topic] = Field(max_length=200)


class MeetingDocument(ResultBase):
    document_kind: Literal['meeting'] = 'meeting'
    status: Literal['draft', 'confirmed'] = 'draft'
    user_edited: bool = False
    decisions: list[EvidenceItem] = Field(max_length=200)
    action_items: list[ManualAction] = Field(max_length=200)
    open_questions: list[EvidenceItem] = Field(max_length=200)
    review_notes: list[str] = Field(max_length=200)


class GeneratedMeeting(Minutes):
    """Model output excludes server-owned metadata and numeric source times."""
    schema_version: Literal[2] = 2
    document_kind: Literal['meeting'] = 'meeting'
    title: str | None = Field(default=None, min_length=1, max_length=200)
    topics: list[GeneratedTopic] = Field(max_length=200)


class VideoClaim(Contract):
    text: str = Field(max_length=10000)
    attribution: str = Field(max_length=1000)
    kind: Literal['reported_fact', 'opinion', 'prediction', 'number']


class VideoDocument(ResultBase):
    document_kind: Literal['video_summary'] = 'video_summary'
    claims: list[VideoClaim] = Field(max_length=200)


class GeneratedVideoTopic(Contract):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10000)
    ranges: list[UtteranceRange] = Field(min_length=1, max_length=100)


class GeneratedVideoClaim(VideoClaim):
    source_segment_ids: list[str] = Field(min_length=1, max_length=100)


class GeneratedVideo(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    schema_version: Literal[2] = 2
    document_kind: Literal['video_summary'] = 'video_summary'
    meeting_id: str
    transcript_version: str
    revision: int = Field(ge=1)
    language: Literal['ko'] = 'ko'
    summary: str = Field(max_length=20000)
    topics: list[GeneratedVideoTopic] = Field(max_length=200)
    claims: list[GeneratedVideoClaim] = Field(max_length=200)


class EditDocument(Contract):
    expected_revision: int = Field(ge=1)
    content: Minutes | MeetingDocument | VideoDocument


def parse_document(value):
    if value.get('schema_version', 1) == 1:
        return Minutes.model_validate(value)
    model = VideoDocument if value.get('document_kind') == 'video_summary' else MeetingDocument
    document = model.model_validate(value)
    return normalize_meeting_topics(document) if isinstance(document, MeetingDocument) else document


def resolve_starts(ranges, reading):
    """Validate IDs, then union intersecting/adjacent chronological ranges.

    Disjoint recurrences retain separate starts. No caller-supplied numeric
    position is accepted, and each output links exactly its selected utterances.
    """
    utterances = reading['utterances']
    positions = {item['id']: index for index, item in enumerate(utterances)}
    if len(positions) != len(utterances):
        raise ValueError('DUPLICATE_UTTERANCE_IDS')
    intervals = []
    for supplied in ranges:
        selected = UtteranceRange.model_validate(supplied)
        if selected.first not in positions or selected.last not in positions:
            raise ValueError('TOPIC_SOURCE_UNAVAILABLE')
        first, last = positions[selected.first], positions[selected.last]
        if first > last:
            raise ValueError('REVERSED_TOPIC_RANGE')
        intervals.append((first, last))
    merged = []
    for first, last in sorted(set(intervals)):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    result = []
    for first, last in merged:
        selected = utterances[first:last + 1]
        result.append(TopicStart(start_ms=min(u['start_ms'] for u in selected),
                                 end_ms=max(u['end_ms'] for u in selected),
                                 utterance_ids=[u['id'] for u in selected]))
    return sorted(result, key=lambda item: (item.start_ms, item.end_ms))


def normalize_meeting_topics(document):
    """Read/materialization adapter; preserves stored revisions and source IDs.

    A sparse evidence gap is not a new discussion. Only another topic inside
    that gap establishes a transition before the same topic returns.
    """
    document = document.model_copy(deep=True)
    originals = [(topic.id, start) for topic in document.topics for start in topic.starts]
    for topic in document.topics:
        merged = []
        for start in sorted(topic.starts, key=lambda item: (item.start_ms, item.end_ms)):
            switched = merged and any(other != topic.id and
                merged[-1].end_ms <= interval.start_ms < start.start_ms
                for other, interval in originals)
            if merged and not switched:
                previous = merged[-1]
                merged[-1] = TopicStart(start_ms=previous.start_ms,
                    end_ms=max(previous.end_ms, start.end_ms),
                    utterance_ids=list(dict.fromkeys(previous.utterance_ids + start.utterance_ids)))
            else:
                merged.append(start)
        topic.starts = merged
    return document


def legacy_document(value, meeting, transcript=None, *, generated_at=None):
    """Read adapter only: never rewrites an immutable schema-1 revision."""
    legacy = Minutes.model_validate(value)
    reading = None
    if transcript is not None:
        try:
            reading = derive_utterances(transcript, legacy.transcript_version)
        except ValueError:
            pass
    source_to_utterance = {source: u['id'] for u in reading['utterances'] for source in u['source_ids']} if reading else {}
    topics = []
    for item in legacy.topics:
        ids = list(dict.fromkeys(source_to_utterance[source] for source in item.source_segment_ids
                                 if source in source_to_utterance))
        starts = resolve_starts([{'first': i, 'last': i} for i in ids], reading) if reading else []
        topics.append(Topic(id=item.id, title='', text=item.text, starts=starts,
                            source_segment_ids=item.source_segment_ids, origin='legacy'))
    actions = []
    for item in legacy.action_items:
        owner = (transcript or {}).get('speakers', {}).get(item.owner_speaker_id, {}).get('name')
        actions.append(ManualAction(**item.model_dump(), owner_name=owner))
    return normalize_meeting_topics(MeetingDocument(meeting_id=legacy.meeting_id, transcript_version=legacy.transcript_version,
        revision=legacy.revision, status=legacy.status, summary=legacy.summary, topics=topics,
        decisions=legacy.decisions, action_items=actions, open_questions=legacy.open_questions,
        review_notes=legacy.review_notes, metadata=DocumentMetadata(
            title=meeting['title'], occurred_at=meeting.get('occurred_at'), timezone=meeting.get('timezone', 'Asia/Seoul'),
            generated_at=generated_at, utterance_rule=RULE_VERSION if reading else None)))
