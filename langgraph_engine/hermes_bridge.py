#!/usr/bin/env python3
import asyncio
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / 'hydrameda'))
PYTHON_BIN = HYDRAMEDA_ROOT / '.venv' / 'bin' / 'python'
GRAPH_SCRIPT = HYDRAMEDA_ROOT / 'langgraph_engine' / 'graph.py'
HERMES_OUTBOX = Path.home() / '.hermes' / 'voice_outbox'
HERMES_OUTBOX.mkdir(parents=True, exist_ok=True)

async def _synthesize_edge_tts(text: str, out_file: Path):
    import edge_tts
    comm = edge_tts.Communicate(text, voice='en-GB-RyanNeural', rate='+5%')
    await comm.save(str(out_file))

def execute_pipeline_job(sender: str, prompt: str, repo_path: str):
    slug = re.sub(r'[^a-z0-9]+', '-', prompt.lower()).strip('-')[:24].rstrip('-')
    task_id = f"hermes-{slug or 'task'}-{int(time.time())}"
    cmd = [str(PYTHON_BIN), str(GRAPH_SCRIPT), '--prompt', prompt, '--repo', repo_path, '--task-id', task_id, '--non-interactive']
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(proc.stdout, flush=True)
    if proc.returncode == 0 and 'status: PASSED' in proc.stdout:
        status = 'PASSED'
    elif 'paused in AWAITING_APPROVAL' in proc.stdout:
        status = 'AWAITING_APPROVAL'
    else:
        status = 'FAILED'
    summary_speech = f'Hydrameda build report for {slug.replace("-", " ")}. Task is {status.replace("_", " ").lower()}.'
    mp3_path = HERMES_OUTBOX / f'{task_id}.mp3'
    try:
        asyncio.run(_synthesize_edge_tts(summary_speech, mp3_path))
    except Exception as e:  # noqa: BLE001 - optional audio synthesis failure should not fail pipeline job
        print(f'Edge-TTS warning: {e}', flush=True)
    manifest_path = HERMES_OUTBOX / f'{task_id}.json'
    manifest_path.write_text(json.dumps({
        'task_id': task_id, 'sender': sender, 'status': status,
        'text_summary': summary_speech, 'audio_file': str(mp3_path),
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
    }, indent=2))
    print(f'🔊 [Stage 5 Complete] {task_id} -> {status} | Audio: {mp3_path}', flush=True)

class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == '/health':
            self._send_json(200, {'status': 'ok', 'service': 'hydrameda-hermes-bridge', 'port': 5679})
        elif self.path == '/outbox':
            items = sorted([p.name for p in HERMES_OUTBOX.glob('*.mp3')])
            self._send_json(200, {'voice_notes': items, 'outbox_dir': str(HERMES_OUTBOX)})
        else:
            self._send_json(404, {'error': 'not found'})
    def do_POST(self):
        if self.path in ('/webhook/hydrameda-trigger', '/execute'):
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length).decode('utf-8') if length > 0 else '{}'
            data = json.loads(raw) if raw else {}
            sender = data.get('sender', 'daman')
            prompt = data.get('prompt', 'Create a URL slug generator with unit tests')
            repo_path = data.get('repo_path', str(HYDRAMEDA_ROOT))
            threading.Thread(target=execute_pipeline_job, args=(sender, prompt, repo_path), daemon=True).start()
            self._send_json(200, {'accepted': True, 'sender': sender, 'prompt': prompt, 'outbox': str(HERMES_OUTBOX)})
        else:
            self._send_json(404, {'error': 'not found'})

if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 5679), BridgeHandler).serve_forever()
