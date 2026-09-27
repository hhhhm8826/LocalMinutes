"""Offline CPU adapters. Imports and model allocations stay in the job process."""
import gc
import os
import re


MODEL_REVISIONS = {
    'asr': ('mobiuslabsgmbh/faster-whisper-large-v3-turbo', '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'),
    'ko_align': ('kresnik/wav2vec2-large-xlsr-korean', '629c9a3501c10ba128bf3fa1eebb12af3be03f61'),
    'diarization': ('pyannote/speaker-diarization-community-1', '3533c8cf8e369892e6b79ff1bf80f7b0286a54ee'),
}


class ModelUnavailable(Exception):
    pass


def alignment_language(value):
    """Mixed/unsupported scripts are deliberately left at source segment timing."""
    letters = [character for character in value if character.isalpha()]
    korean = any('\uac00' <= character <= '\ud7a3' for character in letters)
    english = any(character.isascii() for character in letters)
    unsupported = any(not character.isascii() and not '\uac00' <= character <= '\ud7a3' for character in letters)
    if unsupported or korean == english:
        return None
    return 'ko' if korean else 'en'


def preserve_alignment(source, aligned):
    """Never let alignment drop text or invent timings outside the source interval."""
    fallback = dict(source, words=[], alignment_warning='SEGMENT_TIMING_FALLBACK')
    words = [word for segment in aligned for word in segment.get('words', [])]
    def normalize(value):
        return re.sub(r'\s+', '', value)
    if normalize(''.join(word.get('word', '') for word in words)) != normalize(source['text']):
        return fallback
    last = source['start']
    for word in words:
        start, end = word.get('start'), word.get('end')
        if start is None or end is None:
            continue
        if not (last <= start <= end <= source['end'] + .02):
            return fallback
        last = start
    return dict(source, words=words)


class SpeechModels:
    def __init__(self, settings):
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0', DO_NOT_TRACK='1',
                          TOKENIZERS_PARALLELISM='false', TORCH_HOME=str(settings.cache_dir / 'torch'),
                          OMP_NUM_THREADS=str(settings.threads), MKL_NUM_THREADS=str(settings.threads))
        self.settings = settings
        self.paths = {}

    def cached(self, key):
        from huggingface_hub import snapshot_download
        repo, revision = MODEL_REVISIONS[key]
        try:
            path = snapshot_download(repo, revision=revision, local_files_only=True)
        except Exception as exc:
            raise ModelUnavailable('MODEL_CACHE_REQUIRED') from exc
        self.paths[key] = path
        return path

    def audio(self, path):
        import torch
        import whisperx
        torch.set_num_threads(self.settings.threads)
        return whisperx.load_audio(str(path))

    def transcribe(self, audio, language):
        import whisperx
        model = whisperx.load_model(self.cached('asr'), 'cpu', compute_type='int8',
                                   language=None if language == 'auto' else language,
                                   threads=self.settings.threads, local_files_only=True)
        try:
            actual = model.model.model.compute_type
            if actual not in {'int8', 'int8_float32'} or model.model.model.device != 'cpu':
                raise ModelUnavailable('CPU_INT8_REQUIRED')
            result = model.transcribe(audio, batch_size=1, language=None if language == 'auto' else language)
            return dict(result, device='cpu', compute_type=actual, batch_size=1, threads=self.settings.threads,
                        model_revision=MODEL_REVISIONS['asr'][1],
                        warnings=[] if result['segments'] else ['NO_RECOGNIZED_SPEECH'])
        finally:
            del model
            gc.collect()

    def align(self, audio, transcript):
        import whisperx
        output = [dict(segment, words=[], alignment_warning='UNCERTAIN_LANGUAGE') for segment in transcript['segments']]
        for language in ('ko', 'en'):
            indexes = [i for i, segment in enumerate(transcript['segments'])
                       if alignment_language(segment['text']) == language]
            if not indexes:
                continue
            # Torchaudio defaults may download; require its prepared checkpoint first.
            checkpoints = self.settings.cache_dir / 'torch/hub/checkpoints'
            if language == 'en' and not (checkpoints / 'wav2vec2_fairseq_base_ls960_asr_ls960.pth').is_file():
                raise ModelUnavailable('EN_ALIGNMENT_CACHE_REQUIRED')
            model, metadata = whisperx.load_align_model(language, 'cpu',
                model_name=self.cached('ko_align') if language == 'ko' else None,
                model_dir=str(checkpoints), model_cache_only=True)
            try:
                for index in indexes:
                    source = transcript['segments'][index]
                    try:
                        result = whisperx.align([source], model, metadata, audio, 'cpu')
                        output[index] = preserve_alignment(source, result['segments'])
                    except (ValueError, RuntimeError, IndexError):
                        output[index] = dict(source, words=[], alignment_warning='ALIGNMENT_FAILED')
            finally:
                del model
                gc.collect()
        return {'segments': output, 'revisions': {'ko': MODEL_REVISIONS['ko_align'][1],
                                                'en': 'torchaudio-WAV2VEC2_ASR_BASE_960H'}}

    def diarize(self, audio, speakers):
        import torch
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(self.cached('diarization'))
        pipeline.to(torch.device('cpu'))
        try:
            options = {'num_speakers': speakers} if speakers is not None else {'min_speakers': 1, 'max_speakers': 12}
            result = pipeline({'waveform': torch.from_numpy(audio).unsqueeze(0), 'sample_rate': 16000}, **options)
            def turns(annotation):
                return [{'start_ms': max(0, round(turn.start * 1000)), 'end_ms': round(turn.end * 1000),
                         'speaker_id': speaker} for turn, _, speaker in annotation.itertracks(yield_label=True)]
            labels = result.speaker_diarization.labels()
            if labels and result.speaker_embeddings is None:
                raise ModelUnavailable('SPEAKER_EMBEDDINGS_UNAVAILABLE')
            embeddings = {} if not labels else dict(zip(labels, result.speaker_embeddings.tolist(), strict=True))
            return {'regular': turns(result.speaker_diarization), 'exclusive': turns(result.exclusive_speaker_diarization),
                    'embeddings': embeddings, 'model_revision': MODEL_REVISIONS['diarization'][1], 'options': options}
        finally:
            del pipeline
            gc.collect()
