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
import time

import numpy as np
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from medir_panorama import carregar_mapa, medir_ponto_robusto, calibrar_escala

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_mapas')
os.makedirs(CACHE_DIR, exist_ok=True)

# API_KEY opcional (variavel de ambiente MEDICAO_API_KEY) - protecao minima
# ja' que este endpoint fica exposto na internet antes de existir login/
# sessao de usuario no site. Se nao configurada, roda sem checagem (uso
# local/teste apenas).
API_KEY = os.environ.get('MEDICAO_API_KEY')

app = Flask(__name__)
CORS(app)

_cache_landmarks = {}  # mapa_url -> landmarks_pos (np.array)


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
    _, _, landmarks_pos = carregar_mapa(caminho_cache)
    _cache_landmarks[mapa_url] = landmarks_pos
    return landmarks_pos


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
    """pontos: lista de 2 dicts {pos_w, quat_wc, u, v}. Retorna (resultados,
    erro) - resultados e' a lista de dict de medir_ponto_robusto por ponto
    (na mesma ordem), erro e' None se ambos os pontos deram sucesso, ou uma
    mensagem combinada dos motivos de falha caso contrario."""
    landmarks_pos = _baixar_mapa(mapa_url)
    resultados = []
    for p in pontos:
        keyframe = _pose_do_ponto(p)
        r = medir_ponto_robusto(keyframe, p['u'], p['v'], landmarks_pos,
                                 tolerancia_consistencia=tolerancia_consistencia)
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
    resposta = dict(sucesso=True, distancia_slam=dist_slam,
                     confianca=[r['confianca'] for r in resultados],
                     tempo_s=round(time.time() - t0, 2))
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
