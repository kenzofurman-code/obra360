# -*- coding: utf-8 -*-
# api_medicao.py
# Pequeno servico Flask que expoe a ferramenta de medicao (medir_panorama.py)
# pro site, via HTTP. Roda na mesma VPS que o worker.py --poll (ver
# OBRA360_ROADMAP.md / obra360_hosting_decision). Reusa medir_ponto_robusto()
# sem duplicar nenhuma logica de RANSAC/landmarks - ver commit18/commit19 no
# CLAUDE.md pro histórico de como essa funcao ficou confiável.
#
# Por que precisa disso: a foto equiretangular + a pose (pos_w/quat_wc) ja'
# vem do proprio manifest.json (carregado no navegador, mesmo campo pose_raw
# que super_resolucao.py usa), mas os LANDMARKS 3D (mapa.msg, ~100MB+ por
# vistoria) so' fazem sentido processar no servidor - rodar RANSAC sobre
# dezenas de milhares de pontos 3D no navegador seria lento, e reimplementar
# essa logica em JS duplicaria tudo que ja foi validado em Python.
#
# Fluxo por vistoria (ver integracao em PanoramaViewer.jsx/Visita.jsx):
#   1. Frontend clica 2 pontos na foto - cada ponto vira {pos_w, quat_wc, u, v}
#      (a pose vem do proprio quadro clicado, direto do manifest.json que o
#      navegador ja tem carregado - a API NAO precisa reabrir o manifest).
#   2. POST /medir manda mapa_url (campo do Firestore, ver worker.py) + os 2
#      pontos + a escala (se a vistoria ja foi calibrada) - a API baixa/
#      cacheia o mapa.msg, roda medir_ponto_robusto() nos 2 pontos, devolve
#      distancia em metros (se calibrado) ou so' unidades SLAM brutas, mais
#      confianca/motivo caso a medicao nao seja confiavel.
#   3. POST /calibrar: mesma coisa, mas o body traz largura_real_m (ex.: uma
#      porta que o Pedro sabe que e' 0.80m) em vez de escala - calcula e
#      devolve a escala pro frontend salvar no Firestore da vistoria
#      (escala_slam_metros) e reusar nas medicoes seguintes.
#
# Cache: mapa.msg de cada vistoria fica em disco (CACHE_DIR) apos o 1o
# download - evita rebaixar 100MB+ por clique. Cache tambem em memoria
# (landmarks ja carregados) enquanto o processo do Flask estiver de pe.
#
# ATENCAO - feature NOVA, ainda NAO testada de ponta a ponta (sem VPS/Docker/
# SLAM real disponivel neste ambiente de desenvolvimento):
#   - So' funciona pra vistorias processadas DEPOIS do commit que adicionou
#     o upload de mapa.msg pro R2 em worker.py (campo 'mapa_url' no Firestore
#     - vistorias antigas nao tem esse campo, endpoint deve retornar erro
#     claro nesse caso, nao travar).
#   - Sem autenticacao de usuario ainda (so' a MEDICAO_API_KEY opcional, uma
#     senha compartilhada simples) - suficiente pra uso interno/teste, NAO
#     e' proteção adequada se a VPS for exposta publicamente sem mais nada
#     na frente (ver nota de seguranca em main()).
#
# Uso: python api_medicao.py [--porta 8090]
# Requisitos: pip install flask flask-cors requests (+ os mesmos do
# medir_panorama.py: msgpack scipy numpy)

import argparse
import hashlib
import os
import re
import time

import numpy as np
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from medir_panorama import (carregar_mapa, medir_ponto_robusto, calibrar_escala,
                             medir_por_reprojecao, pose_no_frame_do_mapa,
                             medir_vao_coplanar, reprojetar_landmarks)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_mapas')
os.makedirs(CACHE_DIR, exist_ok=True)

# API_KEY opcional (variavel de ambiente MEDICAO_API_KEY) - protecao minima
# ja' que este endpoint fica exposto na internet antes de existir login/
# sessao de usuario no site. Se nao configurada, roda sem checagem (uso
# local/teste apenas).
API_KEY = os.environ.get('MEDICAO_API_KEY')

app = Flask(__name__)
CORS(app)

_cache_landmarks = {}  # mapa_url -> (keyframes, landmarks_pos)


def _checar_api_key():
    if not API_KEY:
        return None
    if request.headers.get('X-Api-Key') != API_KEY:
        return jsonify(erro="API key invalida ou ausente"), 401
    return None


def _baixar_mapa(mapa_url):
    """Baixa (ou reusa do cache em disco/memoria) os landmarks de uma
    vistoria. Chave de cache = hash da URL - mesma vistoria sempre bate no
    mesmo arquivo local, sem precisar decodificar o visita_id da URL."""
    if mapa_url in _cache_landmarks:
        return _cache_landmarks[mapa_url]
    nome_cache = hashlib.sha1(mapa_url.encode('utf-8')).hexdigest() + '.msg'
    caminho_cache = os.path.join(CACHE_DIR, nome_cache)
    if not os.path.exists(caminho_cache):
        print(f"[api_medicao] Baixando mapa: {mapa_url}")
        t0 = time.time()
        r = requests.get(mapa_url, timeout=120)
        r.raise_for_status()
        with open(caminho_cache, 'wb') as f:
            f.write(r.content)
        print(f"[api_medicao] Mapa baixado ({len(r.content)/1e6:.1f}MB em {time.time()-t0:.1f}s)")
    keyframes, _, landmarks_pos = carregar_mapa(caminho_cache)
    _cache_landmarks[mapa_url] = (keyframes, landmarks_pos)
    return _cache_landmarks[mapa_url]


def _pose_do_ponto(ponto):
    """Converte {pos_w:[x,y,z], quat_wc:[x,y,z,w]} (vindo do pose_raw do
    manifest.json, ja' calculado por gerar_quadros.py) num dict compativel
    com medir_ponto_robusto - MESMO formato usado por
    super_resolucao.py::quadro_para_pose, sem duplicar a conversao."""
    from scipy.spatial.transform import Rotation as Rot
    pos_w = np.array(ponto['pos_w'], dtype=float)
    rot_wc = Rot.from_quat(ponto['quat_wc']).as_matrix()
    return dict(pos_w=pos_w, rot_wc=rot_wc)


def _medir_pontos(mapa_url, pontos, tolerancia_consistencia=0.15):
    """pontos: lista de 2 dicts {u, v, t} (preferido) ou {u, v, pos_w,
    quat_wc} (legado). Retorna (resultados, erro) - resultados e' a lista de
    dict por ponto (na mesma ordem), erro e' None se ambos os pontos deram
    sucesso, ou uma mensagem combinada dos motivos de falha caso contrario.

    FIX 2026-07-17 (item 21 do CLAUDE.md):
    - A pose agora vem preferencialmente do campo 't' (tempo do quadro no
      video, que o manifest.json ja tem): pose_no_frame_do_mapa() interpola
      dos keyframes do PROPRIO mapa.msg - o referencial certo. O caminho
      legado via pose_raw (pos_w/quat_wc) esta num referencial DIFERENTE do
      mapa (~180 graus + translacao) e so' e' usado se 't' nao vier, com
      aviso no resultado.
    - medir_por_reprojecao() substitui medir_ponto_robusto() (o RANSAC de
      plano podia devolver ponto confiante mas errado em nuvem mista)."""
    keyframes, landmarks_pos = _baixar_mapa(mapa_url)
    resultados = []
    for p in pontos:
        keyframe, aviso = None, None
        if p.get('t') is not None:
            keyframe = pose_no_frame_do_mapa(keyframes, float(p['t']))
            if keyframe is None:
                resultados.append(dict(sucesso=False, ponto3d=None, confianca=None,
                                        dispersao=None,
                                        motivo=f"Nenhum keyframe do mapa perto de t={p['t']}s "
                                               "- trecho sem cobertura do SLAM."))
                continue
        elif 'pos_w' in p and 'quat_wc' in p:
            keyframe = _pose_do_ponto(p)
            aviso = ("pose_raw legada (frame da trajetoria, nao do mapa) - "
                     "resultado pode sair deslocado; envie 't' do quadro no ponto.")
        else:
            resultados.append(dict(sucesso=False, ponto3d=None, confianca=None,
                                    dispersao=None,
                                    motivo="Ponto sem 't' nem pos_w/quat_wc."))
            continue
        r = medir_por_reprojecao(keyframe, p['u'], p['v'], landmarks_pos)
        if aviso:
            r['aviso'] = aviso
        resultados.append(r)
    if not all(r['sucesso'] for r in resultados):
        motivos = [r['motivo'] for r in resultados if not r['sucesso']]
        return resultados, '; '.join(motivos)
    return resultados, None


@app.route('/medir', methods=['POST'])
def medir():
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    mapa_url = body.get('mapa_url')
    pontos = body.get('pontos')
    escala = body.get('escala_slam_metros')
    if not mapa_url or not pontos or len(pontos) != 2:
        return jsonify(erro="Informe mapa_url e exatamente 2 pontos."), 400
    t0 = time.time()
    try:
        resultados, erro = _medir_pontos(mapa_url, pontos)
    except Exception as e:
        return jsonify(erro=f"Falha ao processar mapa/medicao: {e}"), 500
    if erro:
        return jsonify(sucesso=False, motivo=erro,
                        detalhes=[{'sucesso': r['sucesso'], 'dispersao': r['dispersao'],
                                   'motivo': r['motivo']} for r in resultados]), 200
    p1, p2 = resultados[0]['ponto3d'], resultados[1]['ponto3d']
    dist_slam = float(np.linalg.norm(p1 - p2))
    # Distancia principal: POR PONTO (cada clique com sua profundidade, regra
    # do oclusor - ver medir_por_reprojecao). Validado na porta de 0.86m:
    # erro 0.2%. A estimativa coplanar (uma profundidade comum pros 2
    # cliques) e' devolvida como AUXILIAR: quando as duas divergem muito, e'
    # sinal de medida incerta (superficie em angulo ou cluster contaminado)
    # - o frontend pode avisar o usuario pra clicar de novo.
    cop = medir_vao_coplanar(resultados[0], resultados[1])
    resposta = dict(sucesso=True, distancia_slam=dist_slam,
                     confianca=[r['confianca'] for r in resultados],
                     tempo_s=round(time.time() - t0, 2))
    if cop['aplicavel']:
        resposta['distancia_slam_coplanar'] = cop['distancia_slam']
        div = abs(dist_slam - cop['distancia_slam']) / max(dist_slam, 1e-9)
        resposta['divergencia_pct'] = round(div * 100, 1)
        if div > 0.20:
            resposta['aviso'] = ("As duas estimativas divergem "
                                 f"{div*100:.0f}% - medida incerta, tente clicar "
                                 "exatamente nas bordas/quinas do que quer medir.")
    if escala:
        resposta['distancia_m'] = dist_slam * float(escala)
    return jsonify(resposta)


@app.route('/calibrar', methods=['POST'])
def calibrar():
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    mapa_url = body.get('mapa_url')
    pontos = body.get('pontos')
    largura_real_m = body.get('largura_real_m')
    if not mapa_url or not pontos or len(pontos) != 2 or not largura_real_m:
        return jsonify(erro="Informe mapa_url, 2 pontos e largura_real_m."), 400
    try:
        resultados, erro = _medir_pontos(mapa_url, pontos)
    except Exception as e:
        return jsonify(erro=f"Falha ao processar mapa/medicao: {e}"), 500
    if erro:
        return jsonify(sucesso=False, motivo=erro), 200
    p1, p2 = resultados[0]['ponto3d'], resultados[1]['ponto3d']
    dist_slam = float(np.linalg.norm(p1 - p2))
    try:
        escala = calibrar_escala(dist_slam, float(largura_real_m))
    except ValueError as e:
        return jsonify(sucesso=False, motivo=str(e)), 200
    return jsonify(sucesso=True, escala_slam_metros=escala, distancia_slam=dist_slam)


# ─── Upload de video direto pro R2 (multipart presigned) ────────────────────
# 2026-07-17: fim do Cloudflare Stream - o video bruto sobe DIRETO do
# navegador pro R2 em partes (presigned URLs geradas aqui; as credenciais do
# R2 nunca vao pro frontend). Ao concluir, o Upload.jsx cria a vistoria com
# video_r2_key + status='na_fila' e o worker.py --poll (mesma VPS) processa.
# Requisito de infra: CORS do bucket R2 precisa permitir PUT do dominio do
# site e expor o header ETag (senao o navegador nao consegue montar a lista
# de partes pro /upload/concluir).

# 100MB/parte -> 46.8GB = ~468 partes (max S3: 10000; minimo por parte 5MB).
# Env var so' pra testes (moto/werkzeug limita o corpo do PUT) - producao usa o padrao.
PARTE_TAMANHO = int(os.environ.get('UPLOAD_PARTE_TAMANHO_MB', '100')) * 1024 * 1024
PRESIGN_EXPIRA_S = 24 * 3600       # upload de dezenas de GB em conexao lenta leva horas


def _r2_client():
    """Cliente S3 do R2 - MESMAS variaveis de ambiente do worker.py/
    gerar_quadros.py (no Coolify: configurar nas Environment Variables)."""
    import boto3
    bucket = os.environ.get('R2_BUCKET_NAME')
    account = os.environ.get('R2_ACCOUNT_ID')
    key = os.environ.get('R2_ACCESS_KEY_ID')
    secret = os.environ.get('R2_SECRET_ACCESS_KEY')
    if not (bucket and key and secret and (account or os.environ.get('R2_ENDPOINT_URL'))):
        raise RuntimeError("R2_BUCKET_NAME/R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/"
                           "R2_SECRET_ACCESS_KEY nao configuradas no ambiente da API.")
    # R2_ENDPOINT_URL: override opcional (testes com S3 local/moto; producao
    # nao precisa - o endpoint padrao do R2 e' derivado do account id).
    endpoint = os.environ.get('R2_ENDPOINT_URL') or f'https://{account}.r2.cloudflarestorage.com'
    s3 = boto3.client('s3', aws_access_key_id=key, aws_secret_access_key=secret,
                       endpoint_url=endpoint, region_name='auto')
    return s3, bucket


@app.route('/upload/iniciar', methods=['POST'])
def upload_iniciar():
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    nome = body.get('nome_arquivo') or 'video.mp4'
    tamanho = int(body.get('tamanho') or 0)
    if tamanho <= 0:
        return jsonify(erro="Informe 'tamanho' (bytes) do arquivo."), 400
    n_partes = max(1, -(-tamanho // PARTE_TAMANHO))  # ceil
    if n_partes > 10000:
        return jsonify(erro=f"Arquivo grande demais ({n_partes} partes > 10000)."), 400
    nome_seguro = re.sub(r'[^A-Za-z0-9._-]', '_', nome)[-80:]
    chave = f"videos/{int(time.time())}_{nome_seguro}"
    try:
        s3, bucket = _r2_client()
        mp = s3.create_multipart_upload(Bucket=bucket, Key=chave,
                                         ContentType=body.get('content_type') or 'video/mp4')
        upload_id = mp['UploadId']
        urls = [s3.generate_presigned_url(
                    'upload_part',
                    Params=dict(Bucket=bucket, Key=chave, UploadId=upload_id,
                                PartNumber=n + 1),
                    ExpiresIn=PRESIGN_EXPIRA_S)
                for n in range(n_partes)]
    except Exception as e:
        return jsonify(erro=f"Falha ao iniciar upload no R2: {e}"), 500
    return jsonify(chave=chave, upload_id=upload_id,
                    parte_tamanho=PARTE_TAMANHO, urls=urls)


@app.route('/upload/concluir', methods=['POST'])
def upload_concluir():
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    chave, upload_id, partes = body.get('chave'), body.get('upload_id'), body.get('partes')
    if not (chave and upload_id and partes):
        return jsonify(erro="Informe chave, upload_id e partes [{numero, etag}]."), 400
    try:
        s3, bucket = _r2_client()
        s3.complete_multipart_upload(
            Bucket=bucket, Key=chave, UploadId=upload_id,
            MultipartUpload=dict(Parts=[
                dict(PartNumber=int(p['numero']), ETag=p['etag']) for p in
                sorted(partes, key=lambda p: int(p['numero']))]))
    except Exception as e:
        return jsonify(erro=f"Falha ao concluir upload no R2: {e}"), 500
    return jsonify(sucesso=True, video_r2_key=chave)


@app.route('/upload/abortar', methods=['POST'])
def upload_abortar():
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    chave, upload_id = body.get('chave'), body.get('upload_id')
    if not (chave and upload_id):
        return jsonify(erro="Informe chave e upload_id."), 400
    try:
        s3, bucket = _r2_client()
        s3.abort_multipart_upload(Bucket=bucket, Key=chave, UploadId=upload_id)
    except Exception as e:
        return jsonify(erro=f"Falha ao abortar upload: {e}"), 500
    return jsonify(sucesso=True)


@app.route('/vistoria/excluir-storage', methods=['POST'])
def vistoria_excluir_storage():
    """Apaga do R2 TUDO que pertence a uma vistoria: o prefixo {visita_id}/
    (panoramas + mini/ + manifest.json + waypoints_corrigidos.json + mapa.msg)
    e o video bruto (video_r2_key, quando a vistoria veio do upload direto).
    Chamado pelo site ANTES de deletar o doc do Firestore (excluirVisitaCompleta
    em visitas.js) - sem isso, cada vistoria excluida deixava ate ~50GB
    orfaos pagando storage pra sempre.

    Protecao importante: visita_id precisa parecer um id de doc do Firestore
    (alfanumerico, >=8 chars). Sem essa checagem, um visita_id vazio/'../'
    viraria um prefixo que varre o bucket inteiro."""
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    visita_id = str(body.get('visita_id') or '')
    video_r2_key = body.get('video_r2_key')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,}', visita_id):
        return jsonify(erro="visita_id invalido (esperado id alfanumerico do Firestore)."), 400
    try:
        s3, bucket = _r2_client()
        removidos = 0
        token = None
        while True:
            kw = dict(Bucket=bucket, Prefix=f'{visita_id}/', MaxKeys=1000)
            if token:
                kw['ContinuationToken'] = token
            page = s3.list_objects_v2(**kw)
            chaves = [dict(Key=o['Key']) for o in page.get('Contents', [])]
            if chaves:
                s3.delete_objects(Bucket=bucket,
                                   Delete=dict(Objects=chaves, Quiet=True))
                removidos += len(chaves)
            if not page.get('IsTruncated'):
                break
            token = page.get('NextContinuationToken')
        video_removido = False
        if video_r2_key and str(video_r2_key).startswith('videos/'):
            s3.delete_object(Bucket=bucket, Key=str(video_r2_key))
            video_removido = True
    except Exception as e:
        return jsonify(erro=f"Falha ao limpar storage: {e}"), 500
    print(f"[excluir] vistoria {visita_id}: {removidos} objetos + "
          f"video={'sim' if video_removido else 'nao'}")
    return jsonify(sucesso=True, objetos_removidos=removidos,
                    video_removido=video_removido)


@app.route('/landmarks_frame', methods=['POST'])
def landmarks_frame():
    """Reprojeta os landmarks do mapa NA FOTO de um quadro (pra o frontend
    sobrepor como pontos - guia de onde da' pra medir + diagnostico da
    convencao de UV). Body: {mapa_url, t} (t = tempo do quadro; a pose vem dos
    keyframes do proprio mapa, ver medir_panorama). Devolve lista de {u,v,dist}
    dos landmarks VISIVEIS (na frente da camera), amostrada pra nao mandar
    dezenas de milhares de pontos pro navegador."""
    erro_auth = _checar_api_key()
    if erro_auth:
        return erro_auth
    body = request.get_json(force=True) or {}
    mapa_url = body.get('mapa_url')
    t = body.get('t')
    max_pontos = int(body.get('max_pontos', 4000))
    if not mapa_url or t is None:
        return jsonify(erro="Informe mapa_url e t (tempo do quadro)."), 400
    try:
        keyframes, landmarks_pos = _baixar_mapa(mapa_url)
        kf = pose_no_frame_do_mapa(keyframes, float(t))
        if kf is None:
            return jsonify(erro=f"Sem keyframe do mapa perto de t={t}s."), 200
        us, vs, dist = reprojetar_landmarks(kf, landmarks_pos)
        # so' os na frente/visiveis: dist finita e positiva (reprojetar ja'
        # devolve tudo; filtra os muito longe que sao ruido)
        import numpy as np
        vis = dist < np.percentile(dist, 95)
        idx = np.where(vis)[0]
        if len(idx) > max_pontos:
            idx = idx[np.linspace(0, len(idx) - 1, max_pontos).astype(int)]
        pts = [{'u': round(float(us[k]), 5), 'v': round(float(vs[k]), 5),
                'dist': round(float(dist[k]), 3)} for k in idx]
    except Exception as e:
        return jsonify(erro=f"Falha ao reprojetar landmarks: {e}"), 500
    return jsonify(pontos=pts, total=len(pts))


@app.route('/saude', methods=['GET'])
def saude():
    return jsonify(status='ok', mapas_em_cache=len(_cache_landmarks))


def main():
    ap = argparse.ArgumentParser(
        description="API de medicao (Obra360) - expoe medir_panorama.py via HTTP pro site.")
    ap.add_argument('--porta', type=int, default=8090)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args()
    if not API_KEY:
        print("[AVISO] MEDICAO_API_KEY nao definida - endpoint roda SEM autenticacao. "
              "Configure essa variavel de ambiente (e o mesmo valor no frontend) antes "
              "de expor esta porta na internet publica - qualquer um poderia gastar "
              "CPU/banda da VPS chamando /medir sem essa protecao minima.")
    app.run(host=args.host, port=args.porta)


if __name__ == '__main__':
    main()
