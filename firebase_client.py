# firebase_client.py
"""
Wrapper para acesso ao Firebase via firebase-admin SDK.
Usado pelo pipeline de processamento local (processar_vistoria.py).
Requer: pip install firebase-admin requests
"""
import json
import os
import tempfile
import requests

try:
    import firebase_admin
    from firebase_admin import credentials, firestore, storage
except ImportError:
    print("[ERRO] firebase-admin nao instalado. Execute: pip install firebase-admin")
    raise

_app = None
_db = None
_bucket = None

def init(service_account_path: str, storage_bucket: str):
    """Inicializa Firebase Admin SDK com credenciais de conta de serviço."""
    global _app, _db, _bucket
    cred = credentials.Certificate(service_account_path)
    _app = firebase_admin.initialize_app(cred, {'storageBucket': storage_bucket})
    _db = firestore.client()
    _bucket = storage.bucket()
    print(f"[Firebase] Conectado ao projeto: {_app.project_id}")

def get_visita(visita_id: str) -> dict:
    """Busca documento da vistoria no Firestore."""
    doc = _db.collection('visitas').document(visita_id).get()
    if not doc.exists:
        raise ValueError(f"Vistoria '{visita_id}' nao encontrada no Firestore.")
    data = doc.to_dict()
    data['id'] = visita_id
    return data

def salvar_waypoints(visita_id: str, waypoints: list, status: str = 'processado'):
    """Salva waypoints corrigidos e atualiza status da vistoria."""
    _db.collection('visitas').document(visita_id).update({
        'waypoints': waypoints,
        'status': status,
    })
    print(f"[Firebase] {len(waypoints)} waypoints salvos. Status: '{status}'.")

def baixar_pdf(url: str) -> str:
    """Baixa PDF de uma URL e retorna o caminho do arquivo temporário."""
    print(f"[Firebase] Baixando planta PDF...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.write(resp.content)
    tmp.close()
    print(f"[Firebase] PDF baixado: {tmp.name}")
    return tmp.name

def baixar_video(url: str, destino: str = None) -> str:
    """Baixa vídeo de uma URL e retorna o caminho do arquivo."""
    if not destino:
        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        destino = tmp.name
        tmp.close()
    print(f"[Firebase] Baixando vídeo (pode demorar alguns minutos)...")
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(destino, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded / total * 100
                    print(f"\r  Progresso: {pct:.1f}%", end='', flush=True)
    print(f"\n[Firebase] Vídeo baixado: {destino}")
    return destino
