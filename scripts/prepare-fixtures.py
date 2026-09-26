"""허가 근거가 확인된 YouTube 기반 샘플만 확보한다. 쿠키/DRM/로그인은 사용하지 않는다."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'LocalMinutesPreflight/0.1'}), timeout=30)


def main():
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func
    root = Path('data/fixtures')
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root/'manifest.json'
    manifest = {'status':'IN_PROGRESS', 'samples':[], 'quality_metrics':'NOT_MEASURED',
                'ground_truth':'No verified transcript/speaker annotations', 'multi_speaker_quality':'NOT_VERIFIED'}
    title = "File:'스맵' 송경호, '적셔갑' 이미지 벗고 싶다.webm"
    query = urllib.parse.urlencode(dict(action='query',format='json',titles=title,prop='imageinfo',iiprop='url|extmetadata|size'))
    meta = json.load(get('https://commons.wikimedia.org/w/api.php?'+query))
    info = next(iter(meta['query']['pages'].values()))['imageinfo'][0]
    assert info['extmetadata']['LicenseShortName']['value'] == 'CC BY 3.0'
    original = root/'ko-original.webm'
    if not original.exists():
        partial = original.with_suffix('.partial')
        with get(info['url']) as response, partial.open('wb') as file:
            while chunk := response.read(1024*1024):
                file.write(chunk)
        partial.replace(original)
    ko = root/'ko-180.webm'
    if not ko.exists():
        subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-ss','30','-i',str(original),'-t','180',
                        '-map','0:v:0','-map','0:a:0','-c','copy',str(ko)], check=True)
    sources = [{'id':'ko-180','language':'ko','path':str(ko),'source_url':'https://www.youtube.com/watch?v=3ncGEQ6IdKQ',
                'download_url':info['url'].split('?')[0], 'license':'CC BY 3.0', 'creator':'fomos esports',
                'license_evidence':info['descriptionurl'],'requested_interval_seconds':[30,210],
                'speaker_count_verified':None, 'modifications':'180 second excerpt and mono WAV conversion'}]
    en = root/'en-180.mp4'
    try:
        if not en.exists():
            opts = {'format':'bv[height<=360][ext=mp4]+ba[ext=m4a]', 'outtmpl':str(en), 'noplaylist':True,
                    'js_runtimes':{'node':{'path':str(Path.home()/'.local/share/local-meeting-minutes/toolchain/node-v24.21.0-linux-x64/bin/node')}},
                    'quiet':True, 'no_warnings':True,
                    'download_ranges':download_range_func(None,[(30,210)]), 'force_keyframes_at_cuts':True}
            with YoutubeDL(opts) as ydl:
                ydl.download(['https://www.youtube.com/watch?v=r-szu88WNSk'])
        sources.append({'id':'en-180','language':'en','path':str(en),
                        'source_url':'https://www.youtube.com/watch?v=r-szu88WNSk',
                        'license':'CC BY (organizer statement)', 'creator':'PyCon Austria / Laurent Direr',
                        'license_evidence':'https://2025.pycon.at/blog/videos-on-youtube/',
                        'requested_interval_seconds':[30,210], 'speaker_count_verified':None,
                        'modifications':'180 second excerpt and mono WAV conversion'})
    except Exception as exc:
        manifest['samples'].append({'id':'en-180','status':'BLOCKED','error_type':type(exc).__name__})
    for source in sources:
        video = Path(source['path'])
        wav = root/(source['id']+'.wav')
        if not wav.exists():
            subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-i',str(video),'-vn','-ac','1','-ar','16000',str(wav)],check=True)
        probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(wav)],capture_output=True,text=True,check=True)
        duration = float(json.loads(probe.stdout)['format']['duration'])
        source.update(status='ACQUIRED' if 150 <= duration <= 210 else 'FAIL_DURATION',
                      duration_seconds=duration, files=[])
        for path in (video,wav):
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream,'sha256').hexdigest()
            source['files'].append({'path':str(path.resolve()),'sha256':checksum,'bytes':path.stat().st_size})
        manifest['samples'].append(source)
    manifest['status'] = 'ACQUIRED_NOT_QUALITY_VERIFIED' if len(sources)==2 and all(s['status']=='ACQUIRED' for s in manifest['samples']) else 'PARTIAL'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
