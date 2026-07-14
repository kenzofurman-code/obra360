# -*- coding: utf-8 -*-
# worker.py
# Automatiza o pipeline completo do Obra360 (Fase 1 do OBRA360_ROADMAP.md):
#   baixa video (R2 ou local) -> extrai trajetoria -> extrai portas do PDF ->
#   map matching (ancora + correcao por porta, MESMA logica do frontend) ->
#   gera panoramas + sobe pro R2 (gerar_quadros.py) -> atualiza status no
#   Firestore. Reaproveita run_trajectory/run_pdf_extractor/run_map_matching
#   de processar_vistoria.py em vez de duplicar a logica de alinhamento.
#
# GAPS CONHECIDOS (nao resolvidos aqui - ver OBRA360_ROADMAP.md Fase 1):
#   1. RESOLVIDO 2026-07-12: rodar_slam.py agora existe (orquestra o
#      stella_vslam via Docker) e o conversor TUM -> raw_waypoints (funcao
#      tum_para_raw_waypoints abaixo, reaproveitando load_tum/project_to_plan
#      de slam_to_obra360.py) plugam a trajetoria de alta precisao no MESMO
#      esquema ancora/heading/escala que o frontend usa - nao foi necessario
#      usar a calibracao propria (--referencia/--gabarito) do slam_to_obra360.py,
#      porque o run_map_matching ja resolve escala/rotacao/origem sozinho.
#      AINDA NAO TESTADO DE PONTA A PONTA (sem Docker neste ambiente) - validar
#      na maquina do Pedro (Docker Desktop + imagem stella_vslam-socket ja
#      construidos la) antes de confiar no resultado em producao.
#   3. --poll depende de status=='na_fila' + campo video_r2_key no Firestore,
#      que o Upload.jsx ainda NAO escreve (fica pra Fase 2 do roadmap, quando
#      o upload for migrado de Cloudflare Stream para R2). Ate la, use o modo
#      manual (--id + --video).
#
# Uso manual (hoje, sem fila - substitui processar_vistoria.py e ja gera panoramas):
#   python worker.py --id <visita_id> --video video.mp4
#
# Uso em fila (depois que o Upload.jsx gravar status=na_fila + video_r2_key):
#   python worker.py --poll --intervalo 15
#
# Requisitos: pip install firebase-admin requests opencv-python numpy pymupdf boto3 python-dotenv

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'serviceAccountKey.json')
STORAGE_BUCKET = 'obras360-c474d.firebasestorage.app'

# Carrega o .env do PROJETO (nao da pasta onde o worker foi chamado - importante
# quando roda via .bat colocado numa pasta de video, longe do repo).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, '.env'))
except ImportError:
    print("[AVISO] python-dotenv nao instalado - as variaveis do .env (R2, etc.) "
          "precisam estar setadas manualmente no ambiente. Execute: pip install python-dotenv")

sys.path.insert(0, SCRIPT_DIR)
import firebase_client
from processar_vistoria import (run_trajectory, run_pdf_extractor, run_map_matching,
                                run_ambientes_extractor, estabilizar_paradas)


# ─── Video: R2 ou local ──────────────────────────────────────────────────────

def baixar_video_r2(chave, destino):
    """Baixa o video bruto do R2. Bucket/credenciais via variaveis de ambiente
    (as mesmas do gerar_quadros.py): R2_BUCKET_NAME, R2_ACCOUNT_ID,
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY."""
    import boto3
    bucket = os.environ.get('R2_BUCKET_NAME')
    account = os.environ.get('R2_ACCOUNT_ID')
    key = os.environ.get('R2_ACCESS_KEY_ID')
    secret = os.environ.get('R2_SECRET_ACCESS_KEY')
    if not (bucket and account and key and secret):
        raise RuntimeError(
            "Configure R2_BUCKET_NAME, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID e "
            "R2_SECRET_ACCESS_KEY no .env para baixar video do R2.")
    s3 = boto3.client('s3', aws_access_key_id=key, aws_secret_access_key=secret,
                       endpoint_url=f'https://{account}.r2.cloudflarestorage.com',
                       region_name='auto')
    print(f"[R2] Baixando video: {chave}")
    s3.download_file(bucket, chave, destino)
    print(f"[R2] Video baixado: {destino}")
    return destino


# ─── Trajetoria: SLAM se disponivel, senao odometria leve (fallback) ────────

def rodar_slam_se_disponivel(video_path, tmp_dir):
    """
    Orquestra o stella_vslam via rodar_slam.py (precisa de Docker + imagem
    stella_vslam-socket - ver rodar_slam.py). Retorna (frame_trajectory.txt,
    mapa.msg) ou (None, None) se o script nao existir ou o Docker falhar
    (ex.: ambiente sem Docker, como um VPS ainda nao provisionado) - nesse
    caso o chamador cai para a odometria leve automaticamente.
    """
    rodar_slam_path = os.path.join(SCRIPT_DIR, 'rodar_slam.py')
    if not os.path.exists(rodar_slam_path):
        print("[SLAM] rodar_slam.py nao encontrado neste repo - usando odometria leve "
              "(process_trajectory.py via processar_vistoria.run_trajectory). Precisao "
              "esperada bem menor que o pipeline SLAM validado no handoff.")
        return None, None
    traj_out = os.path.join(tmp_dir, 'frame_trajectory.txt')
    mapa_out = os.path.join(tmp_dir, 'mapa.msg')
    # --manter-mapa e' OBRIGATORIO por regra operacional (ver OBRA360_SLAM_HANDOFF.md):
    # o mapa.msg alimenta a ferramenta de medicao e o mapa persistente futuro.
    cmd = [sys.executable, rodar_slam_path, '--video', video_path,
           '--out', traj_out, '--manter-mapa', '--mapa-out', mapa_out]
    print(f"[SLAM] Rodando: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[SLAM] Falhou ({e}) - caindo para odometria leve.")
        return None, None
    if not os.path.exists(traj_out):
        print("[SLAM] rodar_slam.py rodou mas nao gerou frame_trajectory.txt - fallback.")
        return None, None
    return traj_out, (mapa_out if os.path.exists(mapa_out) else None)


def tum_para_raw_waypoints(traj_tum_path, proj="xz"):
    """Converte frame_trajectory.txt (TUM: timestamp tx ty tz qx qy qz qw) do
    stella_vslam para [{t,x,y}, ...] no MESMO formato que process_trajectory.py
    produz - sem nenhuma calibracao/ancoragem propria. Reaproveita load_tum e
    project_to_plan de slam_to_obra360.py (ja validados) em vez de duplicar.

    Por que nao precisa de ancoragem aqui: run_map_matching() (processar_vistoria.py)
    ja aplica escala (path_scale), rotacao (heading_offset), espelhamento
    (espelhar_caminho) e origem (ancora1) - a MESMA transformacao configurada
    pelo usuario no site para a odometria leve. A trajetoria do SLAM so precisa
    chegar em unidades/eixos consistentes (2D, mesma origem/escala interna do
    proprio stella_vslam) para essa calibracao por ancoras funcionar igual.

    NOTA (2026-07-12, confirmado num teste real): a convencao de eixo X do
    stella_vslam sai espelhada em relacao ao que o site espera por padrao -
    inverte aqui, uma vez, na fonte, em vez de depender do usuario ligar
    "Espelhar" manualmente pra cada vistoria feita com SLAM. O toggle
    espelhar_caminho continua disponivel no site pra casos excepcionais
    (ex.: percurso feito no sentido contrario), mas o padrao agora ja sai certo.
    """
    from slam_to_obra360 import load_tum, project_to_plan
    ts, pos3d = load_tum(traj_tum_path)
    if ts is None:
        raise RuntimeError(f"Nenhuma pose encontrada em {traj_tum_path} "
                            "(tracking do stella_vslam provavelmente falhou).")
    if len(ts) < 10:
        print(f"[SLAM] [AVISO] Apenas {len(ts)} poses rastreadas - tracking pode "
              "ter falhado ou se perdido no meio do video.")
    P = project_to_plan(pos3d, proj)
    P[:, 0] = -P[:, 0]  # corrige espelhamento do eixo X do stella_vslam (ver nota acima)
    t0 = ts[0]
    return [{"t": round(float(t - t0), 2), "x": float(p[0]), "y": float(p[1])}
            for t, p in zip(ts, P)]


# ─── Corte do inicio do video (tempo parado posicionando a camera) ─────────

def cortar_video_inicio(video_path, segundos, tmp_dir):
    """Corta os primeiros `segundos` do video ANTES de rodar SLAM e
    gerar_quadros - os dois passos usam o video_path resultante daqui, entao
    trajetoria e frames ficam sincronizados em t=0 sem precisar editar o
    video manualmente toda vez que o operador fica parado posicionando a
    camera no comeco da gravacao (ver estabilizar_paradas, que resolve o MESMO
    problema mas so depois que a trajetoria ja foi extraida - isso aqui evita
    o problema na origem).

    Reencodifica (nao usa -c copy) pra cortar exatamente no segundo pedido, e
    nao no keyframe mais proximo - com -c copy sobraria um pedaco parado."""
    if not segundos or segundos <= 0:
        return video_path
    saida = os.path.join(tmp_dir, 'video_cortado.mp4')
    cmd = ['ffmpeg', '-y', '-ss', str(segundos), '-i', video_path,
           '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast', '-an', saida]
    print(f"[Corte] Cortando {segundos}s do inicio do video (ffmpeg)...")
    subprocess.run(cmd, check=True, capture_output=True)
    return saida


# ─── Panoramas (gerar_quadros.py, ja sabe subir pro R2 sozinho) ─────────────

def gerar_panoramas(video_path, waypoints_path, prefixo_r2, out_dir):
    bucket = os.environ.get('R2_BUCKET_NAME')
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'gerar_quadros.py'),
           '--video', video_path, '--trajetoria', waypoints_path,
           '--out', out_dir, '--miniaturas', '256']
    if bucket:
        cmd += ['--r2-bucket', bucket, '--r2-prefix', prefixo_r2]
    else:
        print(f"[AVISO] R2_BUCKET_NAME nao definido - panoramas ficam so locais em {out_dir}")
    print(f"[Quadros] Rodando: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    manifest_path = os.path.join(out_dir, 'manifest.json')
    return manifest_path if os.path.exists(manifest_path) else None


# ─── Pipeline de 1 vistoria ──────────────────────────────────────────────────

def processar_visita(visita_id, video_local=None, corte_inicial_seg=None):
    visita = firebase_client.get_visita(visita_id)

    ancora1 = visita.get('ancora1')
    if not ancora1:
        raise RuntimeError("Ancora A nao definida - configure no site antes de processar.")
    heading_offset = visita.get('heading_offset', 0)
    path_scale = visita.get('path_scale', 0.15)
    # padrao False (nao True): o SLAM ja corrige o proprio espelhamento na fonte
    # (ver nota em tum_para_raw_waypoints) - so usar True aqui se o campo estiver
    # EXPLICITAMENTE gravado assim no Firestore (ex.: vistoria antiga, ou caso
    # excepcional configurado manualmente no site). Mesma convencao do criarVisita
    # em src/lib/visitas.js - manter os dois em sincronia.
    espelhar = visita.get('espelhar_caminho', False)
    # --corte-inicial no CLI tem prioridade; senao usa o campo salvo na propria
    # vistoria (corte_inicial_seg, padrao 0) - assim, uma vez configurado pra
    # uma vistoria (ou vindo do site no futuro), continua valendo em qualquer
    # reprocessamento sem precisar passar o argumento de novo.
    corte_inicial = corte_inicial_seg if corte_inicial_seg is not None else visita.get('corte_inicial_seg', 0)
    planta_url = visita.get('planta_url')
    if not planta_url or not planta_url.lower().split('?')[0].endswith('.pdf'):
        raise RuntimeError("Vistoria sem planta PDF vetorial - obrigatoria para o Map Matching.")

    tmp_dir = tempfile.mkdtemp(prefix=f'obra360_{visita_id}_')
    firebase_client.atualizar_status(visita_id, 'processando')

    try:
        # 1. Video: local (--video) ou baixado do R2 (campo video_r2_key)
        video_path = video_local
        if not video_path:
            chave = visita.get('video_r2_key')
            if not chave:
                raise RuntimeError(
                    "Sem --video local nem campo 'video_r2_key' na vistoria - nada para processar "
                    "(ver GAP 3 no topo do arquivo: o Upload.jsx ainda nao grava esse campo).")
            video_path = os.path.join(tmp_dir, 'video.mp4')
            baixar_video_r2(chave, video_path)

        # 1.5 Corta o inicio parado (posicionamento da camera), se configurado -
        # SLAM e gerar_quadros usam o video_path resultante, entao ficam em sincronia.
        if corte_inicial:
            video_path = cortar_video_inicio(video_path, corte_inicial, tmp_dir)

        # 2. Trajetoria bruta: tenta SLAM (alta precisao), cai para odometria leve
        traj_tum, mapa_msg = rodar_slam_se_disponivel(video_path, tmp_dir)
        if traj_tum:
            print("[SLAM] Convertendo trajetoria TUM -> raw_waypoints...")
            raw_waypoints = tum_para_raw_waypoints(traj_tum)
            raw_json = os.path.join(tmp_dir, 'trajetoria_bruta.json')
            with open(raw_json, 'w', encoding='utf-8') as f:
                json.dump(raw_waypoints, f)
            print(f"[SLAM] Trajetoria SLAM: {len(raw_waypoints)} poses -> {raw_json}")
            if mapa_msg:
                # NUNCA excluir (regra do handoff) - alimenta medir_panorama.py e o
                # mapa persistente futuro (Fase 4). Guarda ao lado dos waypoints por
                # enquanto; upload/persistencia de longo prazo fica pra quando essa
                # feature for exposta no produto.
                print(f"[SLAM] mapa.msg disponivel em: {mapa_msg} (nao excluir)")
        else:
            raw_json = os.path.join(tmp_dir, 'trajetoria_bruta.json')
            raw_waypoints = run_trajectory(video_path, raw_json, rate=0.5)

        # 2.5 Congela trechos parados (ex.: posicionando a camera no inicio do
        # video) - SLAM parado pode derivar/tremer e isso conta como distancia
        # falsa na amostragem do gerar_quadros.py, desencontrando o quadro da
        # posicao real logo no comeco do percurso (ver estabilizar_paradas).
        raw_waypoints = estabilizar_paradas(raw_waypoints)

        # 3. Vaos de porta do PDF vetorial (geometria de arco - extrair_portas.py;
        # aspecto da pagina e' necessario pro Map Matching nao distorcer a
        # trajetoria em paginas nao-quadradas - ver alinhar_ponto)
        pdf_path = firebase_client.baixar_pdf(planta_url)
        vaos_json = os.path.join(tmp_dir, 'vaos.json')
        vaos, aspecto = run_pdf_extractor(pdf_path, vaos_json)
        # Ambientes (nome + area m² -> raio de alcance) do mesmo PDF, pra associar
        # cada waypoint ao comodo onde foi tirado (ver extrair_ambientes.py e a
        # discussao com o Pedro em 2026-07-14 sobre progresso de obra por ambiente).
        ambientes_json = os.path.join(tmp_dir, 'ambientes.json')
        ambientes = run_ambientes_extractor(pdf_path, ambientes_json)
        os.unlink(pdf_path)

        # 4. Map matching (ancora + correcao por porta) - mesma logica do frontend.
        # calibracao traz ancora1/heading_offset/path_scale/espelhar_caminho -
        # PODEM ter sido recalibrados automaticamente por multiplas portas
        # (calibrar_por_portas em processar_vistoria.py), ja que a ancora unica
        # manual e' so um chute (escala do SLAM monocular e' arbitraria a cada
        # video). Gravamos esses valores de volta no Firestore abaixo (passo 6)
        # pra que o site use a MESMA calibracao ao re-exibir a trajetoria.
        waypoints_corrigidos, calibracao = run_map_matching(
            raw_waypoints, vaos, ancora1, heading_offset, path_scale, espelhar,
            aspecto=aspecto, ambientes=ambientes)
        waypoints_json = os.path.join(tmp_dir, 'waypoints_corrigidos.json')
        with open(waypoints_json, 'w', encoding='utf-8') as f:
            json.dump(waypoints_corrigidos, f)

        # 5. Panoramas + upload R2
        out_dir = os.path.join(tmp_dir, 'quadros')
        manifest_path = gerar_panoramas(video_path, waypoints_json, visita_id, out_dir)

        # 6. Atualiza Firestore (1 escrita so). Salva o aspecto da planta tambem -
        # o site (Visita.jsx) precisa do MESMO valor pra desfazer a transformacao
        # sem distorcer (senao teria que re-parsear o PDF no navegador so pra isso).
        dados = {'waypoints': waypoints_corrigidos, 'status': 'processado',
                 'planta_aspecto': aspecto,
                 'ancora1': calibracao['ancora1'],
                 'heading_offset': calibracao['heading_offset'],
                 'path_scale': calibracao['path_scale'],
                 'espelhar_caminho': calibracao['espelhar_caminho'],
                 'ambientes': ambientes}
        r2_public_url = os.environ.get('R2_PUBLIC_URL')
        if manifest_path and r2_public_url:
            dados['manifest_url'] = f"{r2_public_url}/{visita_id}/manifest.json"
        elif manifest_path:
            print("[AVISO] R2_PUBLIC_URL nao definido - manifest_url nao foi salvo no Firestore "
                  f"(panoramas estao em {manifest_path}, so localmente/no bucket).")
        firebase_client.atualizar_campos(visita_id, dados)
        print(f"[OK] Vistoria {visita_id} processada com sucesso.")

    except Exception as e:
        firebase_client.atualizar_status(visita_id, 'erro')
        print(f"[ERRO] Vistoria {visita_id}: {e}")
        raise


# ─── Modo fila (--poll) ──────────────────────────────────────────────────────

def poll_loop(intervalo):
    print(f"[Fila] Observando vistorias com status='na_fila' a cada {intervalo}s "
          f"(Ctrl+C para parar)...")
    while True:
        try:
            pendentes = firebase_client.listar_pendentes('na_fila')
        except Exception as e:
            print(f"[Fila] Erro ao consultar Firestore: {e}")
            pendentes = []
        for v in pendentes:
            print(f"\n[Fila] Processando {v['id']}...")
            try:
                processar_visita(v['id'])
            except Exception:
                pass  # ja logado dentro de processar_visita; segue pra proxima
        time.sleep(intervalo)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Worker automatizado do pipeline Obra360.")
    ap.add_argument('--id', default=None, help="ID da vistoria (modo manual, processa uma vez).")
    ap.add_argument('--video', default=None,
                     help="Video local (modo manual; se omitido, baixa de video_r2_key).")
    ap.add_argument('--poll', action='store_true',
                     help="Modo fila: observa o Firestore continuamente (ver GAP 3 no topo).")
    ap.add_argument('--intervalo', type=float, default=15.0,
                     help="Segundos entre checagens no modo --poll (padrao 15).")
    ap.add_argument('--corte-inicial', type=float, default=None, dest='corte_inicial',
                     help="Segundos a cortar do inicio do video (ex.: tempo parado "
                          "posicionando a camera antes de andar). Se omitido, usa o "
                          "campo corte_inicial_seg salvo na propria vistoria (padrao 0).")
    args = ap.parse_args()

    if not os.path.exists(SERVICE_ACCOUNT):
        print(f"[ERRO] Arquivo de credenciais nao encontrado: {SERVICE_ACCOUNT}")
        sys.exit(1)
    firebase_client.init(SERVICE_ACCOUNT, STORAGE_BUCKET)

    if args.poll:
        poll_loop(args.intervalo)
    elif args.id:
        processar_visita(args.id, video_local=args.video, corte_inicial_seg=args.corte_inicial)
    else:
        print("[ERRO] Use --id <visita_id> [--video ...] para rodar uma vez, ou --poll para a fila.")
        sys.exit(1)


if __name__ == '__main__':
    main()
