"""User edits preserve server-owned provenance without requiring live sources."""
from .documents import MeetingDocument
from .repository import Conflict


def validate_edit(previous, supplied, confirm):
    if not isinstance(previous, MeetingDocument) or not isinstance(supplied, MeetingDocument):
        raise Conflict('SUMMARY_READ_ONLY')
    if (supplied.meeting_id != previous.meeting_id or supplied.transcript_version != previous.transcript_version
            or supplied.revision != previous.revision or supplied.metadata != previous.metadata):
        raise Conflict('MINUTES_SNAPSHOT_MISMATCH')
    candidate = supplied.model_copy(deep=True, update={'status': 'confirmed' if confirm else 'draft', 'user_edited': True})
    all_items = [*candidate.topics, *candidate.decisions, *candidate.action_items, *candidate.open_questions]
    if len({item.id for item in all_items}) != len(all_items):
        raise Conflict('MINUTES_DUPLICATE_IDS')
    old_topics = {item.id: item for item in previous.topics}
    for topic in candidate.topics:
        old = old_topics.get(topic.id)
        if ((old is None and (topic.starts or topic.source_segment_ids))
                or (old is not None and (topic.starts != old.starts or topic.source_segment_ids != old.source_segment_ids))):
            raise Conflict('TOPIC_POSITION_IMMUTABLE')
        topic.origin = old.origin if old and topic == old else 'user_authored'
    for name in ('decisions', 'action_items', 'open_questions'):
        originals = {item.id: item for item in getattr(previous, name)}
        for item in getattr(candidate, name):
            old = originals.get(item.id)
            if old is None or item != old:
                # Editing text, owner or due date makes this a user-authored
                # statement, not newly inferred automatic source evidence.
                item.review_status = 'user_authored'
                item.source_segment_ids = []
                if name == 'action_items':
                    item.owner_speaker_id = None
    return candidate
