"""허가된 짧은 샘플의 CPU 전사·정렬·화자 진단. 제품 파이프라인이 아니다."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time

os.environ.update(PYANNOTE_METRICS_ENABLED='0', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                  OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', TOKENIZERS_PARALLELISM='false')


def main():
    import fcntl
    import psutil
    parser = argparse.ArgumentParser()
    parser.add_argument('--language', choices=['ko','en'], required=True)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 210:
        parser.error('Diagnostic duration must be 5..210 seconds')
    cache = Path.home()/'.cache/local-meeting-minutes'
    os.environ['TORCH_HOME'] = str(cache/'torch')
    if args.offline:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        check = socket.socket()
        check.settimeout(1)
        code = check.connect_ex(('1.1.1.1',443))
        check.close()
        if code != 101:  # isolated network namespace has no route
            raise SystemExit(f'Offline smoke requires isolated namespace (ENETUNREACH), got {code}')
    lock = (cache/'heavy.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    evidence = Path('.workflow/evidence')
    models = json.loads((evidence/'models-cache.json').read_text())['models']
    fixture_manifest = json.loads(Path('data/fixtures/manifest.json').read_text())
    fixture = next(s for s in fixture_manifest['samples'] if s.get('language') == args.language and s['status']=='ACQUIRED')
    audio_path = Path(next(f['path'] for f in fixture['files'] if f['path'].endswith('.wav')))
    expected = next(f['sha256'] for f in fixture['files'] if f['path'].endswith('.wav'))
    with audio_path.open('rb') as stream:
        assert hashlib.file_digest(stream,'sha256').hexdigest() == expected
    process = psutil.Process()
    metrics = {'max_tree_rss_bytes':0, 'swap_before_bytes':psutil.swap_memory().used}
    stop = threading.Event()
    def monitor():
        while not stop.wait(.05):
            try:
                rss = sum(p.memory_info().rss for p in [process,*process.children(recursive=True)] if p.is_running())
                metrics['max_tree_rss_bytes'] = max(metrics['max_tree_rss_bytes'],rss)
            except psutil.Error:
                pass
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    report = {'status':'FAIL', 'language':args.language, 'offline_namespace':args.offline,
              'audio_sha256':expected, 'seconds':args.seconds, 'threads':4, 'batch_size':1,
              'device':'cpu', 'compute_type':'int8', 'quality_ground_truth':None, 'timings':{}}
    tag = args.language + ('-offline' if args.offline else '-online')
    start = time.monotonic()
    try:
        import torch
        import whisperx
        from pyannote.audio import Pipeline
        torch.set_num_threads(4)
        audio = whisperx.load_audio(str(audio_path))[:args.seconds*16000]
        t = time.monotonic()
        asr = whisperx.load_model(models['asr']['path'],'cpu',compute_type='int8',
                                 language=args.language, threads=4,local_files_only=True)
        report['timings']['asr_load'] = time.monotonic()-t
        report['actual_asr_device'] = asr.model.model.device
        report['actual_asr_compute_type'] = asr.model.model.compute_type
        t = time.monotonic()
        transcript = asr.transcribe(audio,batch_size=1,language=args.language)
        report['timings']['asr_inference'] = time.monotonic()-t
        del asr
        gc.collect()
        t = time.monotonic()
        align, metadata = whisperx.load_align_model(args.language,'cpu',
            model_name=models['ko_align']['path'] if args.language=='ko' else None,
            model_dir=str(cache/'torch/hub/checkpoints'),model_cache_only=True)
        report['timings']['align_load'] = time.monotonic()-t
        t = time.monotonic()
        aligned = whisperx.align(transcript['segments'],align,metadata,audio,'cpu')
        report['timings']['align_inference'] = time.monotonic()-t
        del align
        gc.collect()
        t = time.monotonic()
        diar = Pipeline.from_pretrained(models['diarization']['path'])
        diar.to(torch.device('cpu'))
        report['timings']['diarization_load'] = time.monotonic()-t
        t = time.monotonic()
        output = diar({'waveform':torch.from_numpy(audio).unsqueeze(0),'sample_rate':16000},min_speakers=1,max_speakers=12)
        report['timings']['diarization_inference'] = time.monotonic()-t
        report['predicted_speaker_count'] = len(output.speaker_diarization.labels())
        report['segment_count'] = len(transcript['segments'])
        report['aligned_word_count'] = len(aligned.get('word_segments',[]))
        report['exclusive_output_present'] = hasattr(output,'exclusive_speaker_diarization')
        report['status'] = 'PASS' if (report['segment_count'] and report['aligned_word_count'] and
            report['predicted_speaker_count'] and report['actual_asr_compute_type'] in {'int8','int8_float32'}) else 'FAIL'
        report['int8_note'] = 'CTranslate2 x86 CPU INT8 uses FP32 for non-quantized layers (int8_float32)'
        # Public audio only, keep text separate from operational metrics.
        (evidence/f'model-{tag}-transcript.json').write_text(json.dumps(aligned,ensure_ascii=False,indent=2))
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error_message'] = str(exc)[:1000]
        raise
    finally:
        stop.set()
        watcher.join()
        metrics['swap_after_bytes'] = psutil.swap_memory().used
        report['metrics'] = metrics
        report['wall_seconds'] = time.monotonic()-start
        report['rtf'] = report['wall_seconds']/args.seconds
        (evidence/f'model-{tag}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
