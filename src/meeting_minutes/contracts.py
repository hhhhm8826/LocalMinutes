"""API·작업자·저장소에서 공유하는 경계 계약."""
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_TRANSCRIPT_ONLY = "COMPLETED_TRANSCRIPT_ONLY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class Stage(StrEnum):
    VALIDATE = "VALIDATE"
    EXTRACT = "EXTRACT"
    TRANSCRIBE = "TRANSCRIBE"
    ALIGN = "ALIGN"
    DIARIZE = "DIARIZE"
    ATTRIBUTE = "ATTRIBUTE"
    SUMMARIZE = "SUMMARIZE"
    SAVE = "SAVE"


class MeetingCreate(Contract):
    title: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    timezone: str = "Asia/Seoul"
    language: Literal["ko", "en", "auto"] = "auto"
    speakers: int | None = Field(default=None, ge=1, le=12)
    allow_external_text: bool = False

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("유효한 IANA 시간대가 필요합니다") from exc
        return value

    @field_validator("occurred_at")
    @classmethod
    def aware_datetime(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("시간대가 포함된 회의 일시가 필요합니다")
        return value


class Attribution(Contract):
    method: Literal['exclusive_time_overlap']
    short: bool
    ambiguous: bool
    alignment_fallback: bool
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(ge=0)


class Segment(Contract):
    id: str = Field(min_length=1, max_length=80)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(max_length=20000)
    speaker_id: str | None = None
    needs_review: bool = False
    overlap: bool = False
    timing: Literal["word", "segment"] = "segment"
    attribution: Attribution | None = None
    user_edited: bool = False

    @model_validator(mode="after")
    def ordered_time(self):
        if self.end_ms < self.start_ms:
            raise ValueError("종료 시각은 시작 시각 이후여야 합니다")
        return self


class RenameSpeaker(Contract):
    type: Literal['rename']
    speaker_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)


class ReassignSegments(Contract):
    type: Literal['reassign']
    speaker_id: str | None
    segment_ids: list[str] = Field(min_length=1, max_length=1000)


class MergeSpeakers(Contract):
    type: Literal['merge']
    speaker_id: str = Field(min_length=1, max_length=80)
    source_speaker_id: str = Field(min_length=1, max_length=80)


class EditText(Contract):
    type: Literal['text']
    segment_id: str = Field(min_length=1, max_length=80)
    text: str = Field(max_length=20000)


class UndoTranscript(Contract):
    type: Literal['undo']


class TranscriptEdit(Contract):
    expected_revision: int = Field(ge=1)
    operation: Annotated[RenameSpeaker | ReassignSegments | MergeSpeakers | EditText | UndoTranscript,
                         Field(discriminator='type')]


class EvidenceItem(Contract):
    id: str
    text: str = Field(max_length=10000)
    source_segment_ids: list[str] = Field(max_length=100)
    review_status: Literal["needs_review", "verified", "user_authored"] = "needs_review"


class ActionItem(Contract):
    id: str
    task: str = Field(max_length=10000)
    owner_speaker_id: str | None
    due_date: str | None
    due_date_original_expression: str | None
    source_segment_ids: list[str] = Field(max_length=100)
    review_status: Literal["needs_review", "verified", "user_authored"] = "needs_review"


class Minutes(Contract):
    schema_version: Literal[1] = 1
    meeting_id: str
    transcript_version: str
    revision: int = Field(ge=1)
    language: Literal["ko"] = "ko"
    status: Literal["draft", "confirmed"] = "draft"
    summary: str = Field(max_length=20000)
    topics: list[EvidenceItem] = Field(max_length=200)
    decisions: list[EvidenceItem] = Field(max_length=200)
    action_items: list[ActionItem] = Field(max_length=200)
    open_questions: list[EvidenceItem] = Field(max_length=200)
    review_notes: list[str] = Field(max_length=200)


class GenerateMinutes(Contract):
    expected_revision: int = Field(ge=1)
    transcript_version: str = Field(min_length=1, max_length=80)
    allow_external_text: bool = False
    new_draft: bool = False


class EditMinutes(Contract):
    expected_revision: int = Field(ge=1)
    content: Minutes


class ConfirmMinutes(Contract):
    expected_revision: int = Field(ge=1)


class StageContext(Contract):
    job_id: str
    attempt_id: str
    meeting_id: str
    stage: Stage
    input_fingerprint: str


class StageAdapter(Protocol):
    def run(self, context: StageContext) -> dict: ...


class MinutesProvider(Protocol):
    def generate(self, meeting: MeetingCreate, segments: list[Segment]) -> Minutes: ...
