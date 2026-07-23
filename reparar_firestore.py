# -*- coding: utf-8 -*-
# reparar_firestore.py
# Repara o BUG encontrado 2026-07-16: worker.py montava o dict `dados`
# (status, calibracao, ambientes, waypoints_url, manifest_url) mas NUNCA
# chamava firebase_client.atualizar_campos() pra gravar isso no Firestore -
# toda vistoria processada via worker.py ficava com o documento incompleto
# (so' status='processando', sem mais nada), mesmo com o pipeline inteiro
# (SLAM + panoramas + upload R2) rodando com sucesso e terminando sem erro
# nos logs. processar_vistoria.py (script manual mais antigo) sempre chamou
# essa funcao corretamente - a chamada nao foi portada quando worker.py foi
# escrito.
#
# O FIX EM SI ja foi aplicado em worker.py (vale pra PROXIMOS runs). Este
# script aqui e' so' pra reparar vistorias JA PROCESSADAS antes do fix, SEM
# precisar rodar o pipeline inteiro de novo (SLAM/panoramas ja rodaram e
# ja estao no R2 - reprocessar tudo custaria de novo os ~70-80min do video
# inteiro). Refaz so' as etapas baratas que realmente faltaram gravar
# (extracao do PDF/ambientes + map matching + escrita no Firestore -
# segundos, nao minutos), usando o frame_trajectory.txt que o rodar_slam.py
# ja deixou salvo na pasta temp (nunca apagado - regra do handoff).
#
# Uso:
#   python reparar_firestore.py --id <visita_id> --traj-completa <caminho para frame_trajectory.txt>
#
# Exemplo com os caminhos do run mais recente (2026-07-16):
#   python reparar_firestore.py --id Nf1KoXXPByR9G01WvnjO --traj-completa "C:\Users\HomePC\AppData\Local\Temp\obra360_Nf1KoXXPByR9G01WvnjO_1kiueu5q\frame_trajectory.txt"

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'serviceAccountKey.json')
STORAGE_BUCKET = 'obras360-c474d.firebasestorage.app'

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, '.env'))
except ImportError:
    print("[AVISO] python-dotenv nao instalado - variaveis do .env (R2, etc.) "
          "precisam estar setadas manualmente no ambiente.")

sys.path.insert(0, SCRIPT_DIR)
import firebase_client
from worker import tum_para_raw_waypoints, subir_json_r2
from processar_vistoria import (run_pdf_extractor, run_ambientes_extractor,
                                 run_map_matching, estabilizar_paradas)


def main():
    ap = argparse.ArgumentParser(
        description="Refaz APENAS PDF+map matching+Firestore (segundos) pra uma vistoria "
                     "cujo SLAM/panoramas ja rodaram com sucesso mas nao foram gravados "
                     "no Firestore (bug do worker.py corrigido em 2026-07-16).")
    ap.add_argument("--id", required=True, help="ID da vistoria no Firestore.")
    ap.add_argument("--traj-completa", required=True,
                    help="frame_trajectory.txt ja salvo por rodar_slam.py (pasta temp do run anterior).")
    args = ap.parse_args()

    firebase_client.init(SERVICE_ACCOUNT, STORAGE_BUCKET)

    visita = firebase_client.get_visita(args.id)
    ancora1 = visita.get('ancora1')
    if not ancora1:
        raise RuntimeError("Ancora A nao definida nesta vistoria - configure no site antes.")
    heading_offset = visita.get('heading_offset', 0)
    path_scale = visita.get('path_scale', 0.15)
    espelhar = visita.get('espelhar_caminho', False)
    planta_url = visita.get('planta_url')
    if not planta_url:
        raise RuntimeError("Vistoria sem planta_url.")

    print("[Reparar] Convertendo frame_trajectory.txt -> raw_waypoints...")
    raw_waypoints = tum_para_raw_waypoints(args.traj_completa)
    raw_waypoints = estabilizar_paradas(raw_waypoints)
    print(f"[Reparar] {len(raw_waypoints)} poses.")

    tmp_dir_local = os.path.dirname(os.path.abspath(args.traj_completa))
    pdf_path = firebase_client.baixar_pdf(planta_url)
    vaos_json = os.path.join(tmp_dir_local, 'vaos_reparo.json')
    vaos, aspecto, pagina = run_pdf_extractor(pdf_path, vaos_json)
    ambientes_json = os.path.join(tmp_dir_local, 'ambientes_reparo.json')
    ambientes = run_ambientes_extractor(pdf_path, ambientes_json)
    os.unlink(pdf_path)
    print(f"[Reparar] {len(vaos)} vaos de porta, {len(ambientes)} ambientes.")

    waypoints_corrigidos, calibracao = run_map_matching(
        raw_waypoints, vaos, ancora1, heading_offset, path_scale, espelhar,
        aspecto=aspecto, ambientes=ambientes)

    dados = {
        'status': 'processado',
        'planta_aspecto': aspecto,
        'ancora1': calibracao['ancora1'],
        'heading_offset': calibracao['heading_offset'],
        'path_scale': calibracao['path_scale'],
        'espelhar_caminho': calibracao['espelhar_caminho'],
        'ambientes': ambientes,
        'selo_qualidade': calibracao['info'],
    }
    r2_public_url = os.environ.get('R2_PUBLIC_URL')
    waypoints_key = f"{args.id}/waypoints_corrigidos.json"
    if r2_public_url and subir_json_r2(waypoints_corrigidos, waypoints_key):
        dados['waypoints_url'] = f"{r2_public_url}/{waypoints_key}"
    else:
        tamanho = len(json.dumps(waypoints_corrigidos).encode('utf-8'))
        if tamanho < 700_000:
            dados['waypoints'] = waypoints_corrigidos
        else:
            raise RuntimeError(
                f"Trajetoria tem ~{tamanho} bytes - configure R2_PUBLIC_URL/R2_BUCKET_NAME "
                "no .env pra subir como arquivo separado.")

    # manifest.json JA foi upado pro R2 pelo run anterior (gerar_quadros.py) -
    # so' precisa apontar a URL, sem reprocessar panoramas/video.
    if r2_public_url:
        dados['manifest_url'] = f"{r2_public_url}/{args.id}/manifest.json"
    else:
        print("[AVISO] R2_PUBLIC_URL nao definido - manifest_url nao sera gravado "
              "(os panoramas em si ja foram upados no run anterior, se R2_BUCKET_NAME "
              "estava configurado la).")

    firebase_client.atualizar_campos(args.id, dados)
    print(f"\n[OK] Vistoria {args.id} reparada: {len(waypoints_corrigidos)} waypoints, "
          f"{len(ambientes)} ambientes, calibracao e URLs gravados no Firestore.")
    print("Recarregue a pagina da vistoria no site pra conferir.")


if __name__ == "__main__":
    main()
