#!/usr/bin/env python3
# processar_vistoria.py
"""
Pipeline automatizado de processamento de vistorias Obra360.

Uso:
    python processar_vistoria.py --id <vistoria_id>
    python processar_vistoria.py --id <vistoria_id> --video caminho/para/video.mp4
    python processar_vistoria.py --id <vistoria_id> --skip-trajectory (usa waypoints já no Firebase)

Pré-requisitos:
    pip install firebase-admin requests opencv-python numpy pymupdf
    Coloque o arquivo serviceAccountKey.json na pasta do projeto.
"""
import argparse
import json
import math
import os
import sys
import tempfile
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'serviceAccountKey.json')
STORAGE_BUCKET = 'obras360-c474d.firebasestorage.app'


# ─── Helpers ────────────────────────────────────────────────────────────────

def alinhar_ponto(wp_x: float, wp_y: float, ancora1: dict, heading_offset: float,
                  path_scale: float, espelhar: bool, aspecto: float = 1.0) -> tuple:
    """
    Converte coordenadas brutas da odometria (espaço do vídeo) para coordenadas
    normalizadas da planta (0-1), usando a MESMA transformacão que o site React.

    Replica exatamente a função `alinharPonto` do Visita.jsx.

    aspecto = altura/largura da página do PDF da planta (ver pdf_extractor.get_page_aspect).
    NECESSÁRIO porque o espaço normalizado [0,1]x[0,1] é anisotrópico quando a
    página não é quadrada - rotacionar/escalar sem essa correção distorce a
    trajetória (achata um eixo, alarga o outro). A rotação/escala acontece em
    espaço físico (isotrópico, "unidades de largura"); só a saída em y é
    dividida por aspecto pra voltar ao normalizado.
    """
    dx = -wp_x if espelhar else wp_x
    dy = -wp_y
    theta = math.radians(heading_offset + 180)
    rx = dx * math.cos(theta) - dy * math.sin(theta)
    ry = dx * math.sin(theta) + dy * math.cos(theta)
    return (
        ancora1['x'] + rx * path_scale,
        ancora1['y'] + (ry * path_scale) / aspecto
    )


def desalinhar_ponto(px: float, py: float, ancora1: dict, heading_offset: float,
                     path_scale: float, espelhar: bool, aspecto: float = 1.0) -> tuple:
    """
    Inverso de alinhar_ponto: converte coordenadas da planta (0-1) de volta
    para coordenadas brutas da odometria. Ver nota de aspecto em alinhar_ponto.
    """
    rx = (px - ancora1['x']) / path_scale
    ry = ((py - ancora1['y']) * aspecto) / path_scale
    theta = math.radians(heading_offset + 180)
    # Rotacão inversa (transposta de R)
    dx = rx * math.cos(theta) + ry * math.sin(theta)
    dy = -rx * math.sin(theta) + ry * math.cos(theta)
    wp_x = -dx if espelhar else dx
    wp_y = -dy
    return (wp_x, wp_y)


def run_trajectory(video_path: str, output_json: str, rate: float = 0.5):
    """Executa process_trajectory.py e salva JSON de trajetória bruta."""
    from process_trajectory import extract_trajectory
    print(f"\n[Pipeline] Etapa 1/3: Extraindo odometria visual do vídeo...")
    waypoints = extract_trajectory(video_path, sample_rate=rate)
    if not waypoints:
        raise RuntimeError("Falha ao extrair trajetória do vídeo.")
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(waypoints, f)
    print(f"[Pipeline] Trajetória bruta: {len(waypoints)} waypoints -> {output_json}")
    return waypoints


def run_pdf_extractor(pdf_path: str, output_json: str):
    """Executa pdf_extractor.py e salva JSON de passagens. Retorna (passagens, aspecto)
    - aspecto (altura/largura da página) é necessário pro Map Matching não distorcer
    a trajetória (ver nota em alinhar_ponto)."""
    from pdf_extractor import extract_doors, get_page_aspect
    print(f"\n[Pipeline] Etapa 2/3: Extraindo vãos de portas do PDF...")
    passagens = extract_doors(pdf_path)
    if not passagens:
        raise RuntimeError("Nenhuma passagem encontrada no PDF.")
    aspecto = get_page_aspect(pdf_path)
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(passagens, f)
    print(f"[Pipeline] Passagens extraídas: {len(passagens)} vãos -> {output_json} "
          f"(aspecto da página: {aspecto:.4f})")
    return passagens, aspecto


def run_map_matching(raw_waypoints: list, passagens: list,
                     ancora1: dict, heading_offset: float,
                     path_scale: float, espelhar: bool,
                     snap_threshold: float = 0.04, aspecto: float = 1.0) -> list:
    """
    Etapa 3/3: Alinha a trajetória na planta usando âncoras do Firebase,
    detecta passagens de porta e aplica correções por Map Matching.
    Remove dependência do gabarito manual.

    aspecto = altura/largura da página (ver pdf_extractor.get_page_aspect) - sem
    isso a distância até as portas (e a trajetória toda) fica distorcida em
    páginas não-quadradas (ver nota em alinhar_ponto).
    """
    print(f"\n[Pipeline] Etapa 3/3: Map Matching com âncoras do Firebase...")
    print(f"  Âncora A: {ancora1}")
    print(f"  Heading Offset: {heading_offset}°")
    print(f"  Path Scale: {path_scale}")
    print(f"  Espelhar: {espelhar}")
    print(f"  Aspecto da página: {aspecto:.4f}")

    # Projeta trajetória bruta para espaço da planta
    aligned = []
    for wp in raw_waypoints:
        px, py = alinhar_ponto(wp['x'], wp['y'], ancora1, heading_offset,
                               path_scale, espelhar, aspecto)
        aligned.append({'t': wp['t'], 'x': px, 'y': py})

    # Detecta passagens de porta mais próximas (distância em espaço FÍSICO -
    # multiplica o delta em y por aspecto pra não subestimar/superestimar
    # distância em páginas não-quadradas)
    passagens_detectadas = []
    for gate in passagens:
        gx, gy = gate['x_norm'], gate['y_norm']
        best_t, best_dist = None, float('inf')
        for pt in aligned:
            d = math.sqrt((pt['x'] - gx)**2 + ((pt['y'] - gy) * aspecto)**2)
            if d < best_dist:
                best_dist = d
                best_t = pt['t']
        if best_dist < snap_threshold:
            passagens_detectadas.append({'t': best_t, 'gate': gate, 'dist': best_dist})

    passagens_detectadas = sorted(passagens_detectadas, key=lambda x: x['t'])
    # Remove duplicatas temporais (intervalo mínimo de 4s)
    filtradas = []
    for p in passagens_detectadas:
        if not filtradas or (p['t'] - filtradas[-1]['t']) > 4.0:
            filtradas.append(p)

    print(f"  Portas detectadas: {len(filtradas)}")
    for p in filtradas:
        print(f"  t={p['t']:.1f}s | {p['gate'].get('codigo','?')} | dist={p['dist']*100:.2f}%")

    # Cria vetores de correção para snap nas portas
    correcoes = []
    for p in filtradas:
        pt_al = next(pt for pt in aligned if pt['t'] == p['t'])
        cx = p['gate']['x_norm'] - pt_al['x']
        cy = p['gate']['y_norm'] - pt_al['y']
        correcoes.append((p['t'], cx, cy))

    # Aplica interpolacão linear das correções em toda a trajetória
    corrigida_planta = []
    for pt in aligned:
        t = pt['t']
        if len(correcoes) == 0:
            cx, cy = 0.0, 0.0
        elif t <= correcoes[0][0]:
            cx, cy = correcoes[0][1], correcoes[0][2]
        elif t >= correcoes[-1][0]:
            cx, cy = correcoes[-1][1], correcoes[-1][2]
        else:
            for i in range(len(correcoes) - 1):
                t0, cx0, cy0 = correcoes[i]
                t1, cx1, cy1 = correcoes[i+1]
                if t0 <= t <= t1:
                    w = (t - t0) / (t1 - t0)
                    cx = (1-w)*cx0 + w*cx1
                    cy = (1-w)*cy0 + w*cy1
                    break
        evento = 'caminho'
        extra = {}
        for p in filtradas:
            if abs(pt['t'] - p['t']) < 0.1:
                evento = 'passagem'
                extra = {'passagem_id': p['gate']['id'], 'codigo': p['gate'].get('codigo','')}
        corrigida_planta.append({'t': pt['t'], 'x': round(pt['x'] + cx, 5),
                                  'y': round(pt['y'] + cy, 5), 'evento': evento, **extra})

    # Converte de volta para espaço bruto da odometria para compatibilidade com o site
    corrigida_raw = []
    for pt in corrigida_planta:
        wx, wy = desalinhar_ponto(pt['x'], pt['y'], ancora1, heading_offset,
                                   path_scale, espelhar, aspecto)
        corrigida_raw.append({
            't': pt['t'],
            'x': round(wx, 5),
            'y': round(wy, 5),
            'label': '',
            'observacao': '',
            'evento': pt.get('evento', 'caminho'),
            **{k: v for k, v in pt.items() if k in ('passagem_id', 'codigo')}
        })

    print(f"  Trajetória corrigida: {len(corrigida_raw)} pontos")
    return corrigida_raw


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Pipeline automatizado Obra360: vídeo 360 + planta PDF → trajetória corrigida no Firebase.')
    parser.add_argument('--id', required=True, help='ID da vistoria no Firestore')
    parser.add_argument('--video', default=None,
        help='Caminho local do vídeo MP4 (opcional; se omitido, tenta baixar da URL da vistoria)')
    parser.add_argument('--skip-trajectory', action='store_true',
        help='Pula etapa de odometria e usa os waypoints já salvos no Firebase')
    parser.add_argument('--rate', type=float, default=0.5,
        help='Taxa de amostragem EKF em segundos (padrão: 0.5)')
    parser.add_argument('--snap-threshold', type=float, default=0.04,
        help='Raio de snap para portas em coordenadas normalizadas (padrão: 0.04)')
    args = parser.parse_args()

    # Verifica credenciais Firebase
    if not os.path.exists(SERVICE_ACCOUNT):
        print(f"[ERRO] Arquivo de credenciais não encontrado: {SERVICE_ACCOUNT}")
        print("Gere em: Firebase Console → Configurações → Contas de serviço → Gerar nova chave privada")
        sys.exit(1)

    # Inicializa Firebase
    import firebase_client
    firebase_client.init(SERVICE_ACCOUNT, STORAGE_BUCKET)

    # Lê dados da vistoria
    print(f"\n[Pipeline] Buscando vistoria '{args.id}'...")
    visita = firebase_client.get_visita(args.id)

    ancora1 = visita.get('ancora1')
    if not ancora1:
        print("[ERRO] Âncora A não definida na vistoria. Configure-a no site primeiro.")
        sys.exit(1)

    heading_offset = visita.get('heading_offset', 0)
    path_scale = visita.get('path_scale', 0.15)
    # padrao False - ver mesmo comentario/motivo em worker.py::processar_visita
    espelhar = visita.get('espelhar_caminho', False)
    planta_url = visita.get('planta_url')

    print(f"  Pavimento: {visita.get('pavimento', '?')}")
    print(f"  Âncora A: {ancora1}")
    print(f"  Heading Offset: {heading_offset}°")
    print(f"  Path Scale: {path_scale}")
    print(f"  Espelhar: {espelhar}")
    print(f"  Planta URL: {'sim' if planta_url else 'NÃO DEFINIDA'}")

    tmp_dir = tempfile.mkdtemp(prefix='obra360_')

    # ── Etapa 0: Obter video e waypoints brutos ──────────────────────────────
    if args.skip_trajectory:
        print("\n[Pipeline] Etapa 1/3: Usando waypoints já existentes no Firebase (--skip-trajectory).")
        raw_waypoints = visita.get('waypoints', [])
        if not raw_waypoints:
            print("[ERRO] Nenhum waypoint encontrado na vistoria. Remova --skip-trajectory.")
            sys.exit(1)
        print(f"  {len(raw_waypoints)} waypoints carregados do Firestore.")
    else:
        # Obtém vídeo
        video_path = args.video
        if not video_path:
            video_url = visita.get('hls_url', '')
            # Tenta construir URL de download direto (Cloudflare Stream não suporta download,
            # portanto o usuário deve passar --video explicitamente por enquanto)
            print("[AVISO] URL de download direto do vídeo não disponível automaticamente.")
            print("  Cloudflare Stream usa HLS (streaming apenas). Forneça o arquivo local com:")
            print(f"  python processar_vistoria.py --id {args.id} --video <caminho_do_video.mp4>")
            sys.exit(1)

        if not os.path.exists(video_path):
            print(f"[ERRO] Arquivo de vídeo não encontrado: {video_path}")
            sys.exit(1)

        raw_json = os.path.join(tmp_dir, 'trajetoria_bruta.json')
        raw_waypoints = run_trajectory(video_path, raw_json, rate=args.rate)

    # ── Etapa 2: Extrair portas do PDF ──────────────────────────────────────
    if not planta_url:
        print("[ERRO] A vistoria não tem planta PDF cadastrada. Faça o upload no site.")
        sys.exit(1)

    # Verifica se é PDF
    is_pdf = planta_url.lower().split('?')[0].endswith('.pdf')
    if not is_pdf:
        print("[AVISO] A planta da vistoria não é um PDF (pode ser imagem).")
        print("  Map Matching com imagem ainda não suportado. Usando trajetória sem correção.")
        # Salva trajetória bruta sem map matching
        waypoints_final = [{
            't': wp['t'], 'x': round(wp['x'], 5), 'y': round(wp['y'], 5),
            'label': '', 'observacao': '', 'evento': 'caminho'
        } for wp in raw_waypoints]
        firebase_client.salvar_waypoints(args.id, waypoints_final, status='processado')
        print(f"\n✅ Concluído (sem map matching): {len(waypoints_final)} waypoints salvos.")
        return

    pdf_path = firebase_client.baixar_pdf(planta_url)
    passagens_json = os.path.join(tmp_dir, 'passagens.json')
    passagens, aspecto = run_pdf_extractor(pdf_path, passagens_json)
    os.unlink(pdf_path)  # Limpa PDF temporário

    # ── Etapa 3: Map Matching ────────────────────────────────────────────────
    waypoints_corrigidos = run_map_matching(
        raw_waypoints, passagens, ancora1, heading_offset,
        path_scale, espelhar, snap_threshold=args.snap_threshold, aspecto=aspecto
    )

    # ── Salva resultado no Firebase ──────────────────────────────────────────
    firebase_client.salvar_waypoints(args.id, waypoints_corrigidos, status='processado')

    print(f"\n[OK] Pipeline concluido com sucesso!")
    print(f"   Vistoria ID: {args.id}")
    print(f"   Waypoints salvos: {len(waypoints_corrigidos)}")
    print(f"   Recarregue a pagina no site para ver a trajetoria corrigida.")


if __name__ == '__main__':
    main()
