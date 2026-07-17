# -*- coding: utf-8 -*-
# gerar_quadros.py
# Extrai panoramas estaticos de um video 360 usando a trajetoria calibrada:
#   - amostragem por DISTANCIA PERCORRIDA (nao por tempo): quadros uniformes
#     no espaco, sem desperdicio quando parado nem rarefacao quando rapido
#   - PAUSAS longas ganham quadro extra (parada do inspetor = ponto de interesse)
#   - dentro da janela de cada waypoint, seleciona o frame MAIS NITIDO
#     (variancia do Laplaciano) para descartar blur de movimento
# Saida: pasta com JPEG/WebP em resolucao original + manifest.json
#   [{id, t, x, y, arquivo, nitidez}, ...] pronto para o front do Obra360.
#
# Uso:
#   python gerar_quadros.py --video v.mp4 --trajetoria caminho.json --out quadros/
#   [--intervalo 0.025] [--pausa-min 4] [--janela 0.5] [--formato jpg]
#   [--qualidade 92] [--miniaturas 1024]
#   [--upscale-metodo {none,bicubic,espcn}] [--upscale-escala 2]  (ver upscale_quadros.py)

import os
import sys
import json
import time
import argparse
import mimetypes
import numpy as np

try:
    import cv2
except ImportError:
    print("Erro: instale as dependencias: pip install opencv-python numpy")
    sys.exit(1)

# Leitura do video via ffmpeg (video_io.py) em vez de cv2.VideoCapture direto -
# ver motivo completo em video_io.py (opencv-python pra Windows falha com
# alguns codecs, ex.: ProRes; ffmpeg do sistema e' agnostico de codec).
from video_io import FFmpegVideoReader, extrair_frame_no_tempo, probe_video

# Upscale opcional (bicubic/ESPCN) - discussao com o Pedro em 2026-07-16 sobre
# EDSR/TensorFlow "pra aumentar a densidade de todos os frames". Testamos
# bicubic/ESPCN/LapSRN/EDSR contra fotos reais do P070 (ver upscale_quadros.py
# e CLAUDE.md pro resultado completo): EDSR e LapSRN ficaram de fora (custo
# de CPU e memoria inviaveis pra ~1000+ fotos/vistoria, sem ganho medido);
# so' bicubic/ESPCN sao oferecidos aqui - e' upscale COSMETICO (zoom menos
# pixelado no viewer), nao recupera detalhe real. Import lazy (so' se
# --upscale-metodo != none) pra nao virar dependencia obrigatoria do pipeline
# principal (mesmo espirito do import de scipy em carregar_poses_tum).
def _upscale_imagem_lazy(frame_bgr, metodo, escala, modelos_dir=None):
    from upscale_quadros import upscale_imagem
    return upscale_imagem(frame_bgr, metodo, escala, modelos_dir)


def carregar_trajetoria(path):
    with open(path, "r", encoding="utf-8") as f:
        wps = json.load(f)
    t = np.array([w["t"] for w in wps], float)
    P = np.array([[w["x"], w["y"]] for w in wps], float)
    order = np.argsort(t)
    return t[order], P[order]


def alvos_por_distancia(t, P, intervalo, pausa_min):
    """Gera os instantes-alvo: um a cada `intervalo` (unid. da planta) de
    percurso + um no meio de cada pausa >= pausa_min segundos."""
    passos = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(passos)])
    alvos = []
    # por distancia
    marcos = np.arange(0.0, s[-1] + 1e-12, intervalo)
    for m in marcos:
        i = np.searchsorted(s, m)
        i = min(i, len(t) - 1)
        alvos.append((float(t[i]), "percurso"))
    # pausas: velocidade ~0 por >= pausa_min
    dt = np.diff(t)
    vel = passos / np.maximum(dt, 1e-9)
    limiar_v = max(np.percentile(vel[vel > 0], 40) * 0.15, 1e-6)
    parado = vel < limiar_v
    i = 0
    while i < len(parado):
        if parado[i]:
            j = i
            while j + 1 < len(parado) and parado[j + 1]:
                j += 1
            dur = t[j + 1] - t[i]
            if dur >= pausa_min:
                alvos.append((float((t[i] + t[j + 1]) / 2), "pausa"))
            i = j + 1
        else:
            i += 1
    # dedupe: alvos a menos de 0.5s um do outro viram um so (pausa tem prioridade)
    alvos.sort()
    dedup = []
    for tv, tipo in alvos:
        if dedup and tv - dedup[-1][0] < 0.5:
            if tipo == "pausa":
                dedup[-1] = (tv, tipo)
            continue
        dedup.append((tv, tipo))
    return dedup


def nitidez(gray_small):
    return float(cv2.Laplacian(gray_small, cv2.CV_64F).var())


def carregar_poses_tum(path):
    """
    Le frame_trajectory.txt (TUM: t tx ty tz qx qy qz qw, uma linha por frame
    RASTREADO pelo SLAM) e devolve (ts, pos_w [N,3], quat_wc [N,4]) em
    unidades BRUTAS do SLAM (mesmo espaco/escala de mapa.msg) - servem so'
    pra anexar uma pose por quadro no manifest.json (campo pose_raw), pro
    super_resolucao.py reprojetar pontos 3D sem precisar do video bruto
    depois. NAO tem nada a ver com a calibracao ancora/heading/escala do
    floor plan (essa e' aplicada em cima de x/y separadamente, ja calculados
    antes desta funcao ser chamada).

    Mesma formula de conversao ja usada em medir_panorama.py e
    super_resolucao.py (confirmada em github.com/stella-cv/stella_vslam/
    discussions/614): rot_wc = rot_cw.T ; pos_w = -rot_wc @ trans_cw.

    Import de scipy e' LAZY (só acontece se --traj-completa for passado) -
    scipy NAO e' dependencia do pipeline principal, so' das ferramentas
    exploratorias (medir_panorama.py/super_resolucao.py). Isso preserva o
    comportamento de sempre do gerar_quadros.py pra quem nao passar esse
    argumento (ex.: fallback de odometria leve, sem frame_trajectory.txt).

    BUG CORRIGIDO 2026-07-16 (achado pelo Pedro - dist_pose_s saiu com
    ~1.78 BILHAO de segundos numa vistoria real): o stella_vslam, quando
    rodado sem --start-timestamp (nosso caso - ver rodar_slam.py), usa
    "system timestamp" (relogio de parede real, tipo epoch Unix ~2026) em
    vez de um tempo relativo ao video - o proprio log do stella_vslam ja
    avisa isso ("--start-timestamp is not set. using system timestamp.").
    Toda outra leitura de frame_trajectory.txt no pipeline (ver
    tum_para_raw_waypoints em worker.py) sempre normaliza subtraindo
    ts[0] antes de usar - esta funcao tinha esquecido esse passo, entao
    pose_mais_perto() comparava um "t" pequeno (fidx/fps, tipo 0-560s)
    contra um "ts" gigante (bilhoes) e sempre batia num indice arbitrario/
    errado. Fix: normaliza ts subtraindo ts[0], igual ao resto do pipeline.
    """
    from scipy.spatial.transform import Rotation as RotLocal
    ts, pos_w_lista, quat_wc_lista = [], [], []
    with open(path, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            partes = linha.split()
            if len(partes) < 8:
                continue
            tv, tx, ty, tz, qx, qy, qz, qw = map(float, partes[:8])
            trans_cw = np.array([tx, ty, tz])
            rot_cw = RotLocal.from_quat([qx, qy, qz, qw]).as_matrix()
            rot_wc = rot_cw.T
            pos_w = -rot_wc @ trans_cw
            quat_wc = RotLocal.from_matrix(rot_wc).as_quat()
            ts.append(tv)
            pos_w_lista.append(pos_w)
            quat_wc_lista.append(quat_wc)
    if not ts:
        return None
    ts = np.array(ts)
    ordem = np.argsort(ts)
    ts = ts[ordem]
    ts = ts - ts[0]  # normaliza pra relativo ao 1o frame rastreado (ver aviso acima)
    return ts, np.array(pos_w_lista)[ordem], np.array(quat_wc_lista)[ordem]


def pose_mais_perto(ts_poses, t_alvo):
    """Indice da pose com timestamp mais proximo de t_alvo (ts_poses ja' esta
    ordenado - busca binaria + checagem dos 2 vizinhos, mais barato que
    argmin numa trajetoria densa com dezenas de milhares de poses)."""
    idx = int(np.searchsorted(ts_poses, t_alvo))
    candidatos = [c for c in (idx - 1, idx) if 0 <= c < len(ts_poses)]
    return min(candidatos, key=lambda c: abs(ts_poses[c] - t_alvo))


def subir_para_r2(pasta_local, bucket, prefix, tem_miniaturas):
    """Sobe os quadros para o Cloudflare R2 (S3-compativel) em <prefix>/...
    O manifest.json e' enviado POR ULTIMO: o site pode usar a existencia dele
    como sinal de que a vistoria esta completa e pronta para exibir."""
    try:
        import boto3
    except ImportError:
        print("[R2] Instale o boto3: pip install boto3")
        sys.exit(1)
    account = os.environ.get("R2_ACCOUNT_ID")
    key = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("R2_ENDPOINT") or (
        f"https://{account}.r2.cloudflarestorage.com" if account else None)
    if not (key and secret):
        print("[R2] Defina R2_ACCESS_KEY_ID e R2_SECRET_ACCESS_KEY (e R2_ACCOUNT_ID "
              "ou R2_ENDPOINT) no ambiente. Upload cancelado; os arquivos locais estao prontos.")
        return
    kwargs = dict(aws_access_key_id=key, aws_secret_access_key=secret)
    if endpoint:
        kwargs.update(endpoint_url=endpoint, region_name="auto")
    s3 = boto3.client("s3", **kwargs)

    def put(caminho_local, chave):
        ctype = mimetypes.guess_type(caminho_local)[0] or "application/octet-stream"
        with open(caminho_local, "rb") as f:
            s3.put_object(Bucket=bucket, Key=chave, Body=f, ContentType=ctype)

    t0 = time.time()
    arquivos = sorted(a for a in os.listdir(pasta_local)
                      if a != "manifest.json" and os.path.isfile(os.path.join(pasta_local, a)))
    print(f"[R2] Subindo {len(arquivos)} quadros para {bucket}/{prefix}/ ...")
    for i, a in enumerate(arquivos):
        put(os.path.join(pasta_local, a), f"{prefix}/{a}")
        if (i + 1) % 50 == 0:
            decorrido = time.time() - t0
            print(f"[R2]   {i+1}/{len(arquivos)} | {decorrido:.0f}s decorridos")
    if tem_miniaturas:
        minis = sorted(os.listdir(os.path.join(pasta_local, "mini")))
        print(f"[R2] Subindo {len(minis)} miniaturas...")
        for a in minis:
            put(os.path.join(pasta_local, "mini", a), f"{prefix}/mini/{a}")
    put(os.path.join(pasta_local, "manifest.json"), f"{prefix}/manifest.json")
    print(f"[R2] Concluido. Manifest em: {prefix}/manifest.json")
    print(f"[R2] [TIMING] Upload total: {time.time() - t0:.1f}s")


def main():
    ap = argparse.ArgumentParser(description="Video 360 + trajetoria -> panoramas indexados")
    ap.add_argument("--video", required=True)
    ap.add_argument("--trajetoria", required=True, help="JSON calibrado [{t,x,y},...]")
    ap.add_argument("--out", default=None,
                    help="Pasta local de saida (padrao: quadros_<nome_do_video>)")
    ap.add_argument("--intervalo", type=float, default=0.025,
                    help="Espacamento entre quadros em unidades da planta "
                         "(padrao 0.025 ~ 1 m num pavimento tipico)")
    ap.add_argument("--pausa-min", type=float, default=4.0,
                    help="Pausa mais longa que isso (s) ganha quadro extra (padrao 4)")
    ap.add_argument("--janela", type=float, default=0.5,
                    help="Meia-janela (s) para buscar o frame mais nitido (padrao 0.5)")
    ap.add_argument("--video-analise", default=None,
                    help="Copia REDUZIDA do mesmo video (ex.: a que o rodar_slam.py ja gera "
                         "pro stella_vslam, ver --video-reduzido-out). Se dada, a varredura "
                         "de nitidez decodifica ELA (rapida) em vez do video full-res "
                         "inteiro, e so' os ~frames ESCOLHIDOS sao extraidos do original "
                         "via seek (extrair_frame_no_tempo). Motivo: o decode full-res era "
                         "52%% do tempo total do pipeline (medido 2026-07-16, ver CLAUDE.md) "
                         "- a nitidez ja era calculada numa versao 640px do frame, entao a "
                         "selecao na copia reduzida escolhe praticamente os mesmos frames. "
                         "Sem a flag: comportamento identico ao de antes.")
    ap.add_argument("--formato", choices=["jpg", "webp"], default="jpg")
    ap.add_argument("--qualidade", type=int, default=92)
    ap.add_argument("--miniaturas", type=int, default=0,
                    help="Se >0, gera tambem miniaturas com esta largura (px)")
    ap.add_argument("--r2-bucket", default=None,
                    help="Bucket do Cloudflare R2 para upload automatico. Credenciais via "
                         "variaveis de ambiente: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                         "R2_SECRET_ACCESS_KEY (nunca no codigo).")
    ap.add_argument("--r2-prefix", default=None,
                    help="Pasta no bucket (padrao: nome do arquivo de video)")
    ap.add_argument("--traj-completa", default=None,
                    help="frame_trajectory.txt do stella_vslam (pose POR FRAME, TUM) - "
                         "OPCIONAL. Se passado, cada quadro ganha um campo pose_raw no "
                         "manifest.json (posicao+rotacao brutas do SLAM), usado depois "
                         "pelo super_resolucao.py. Sem isso, os panoramas saem identicos "
                         "a antes - so' fica indisponivel a super-resolucao por foto.")
    ap.add_argument("--upscale-metodo", choices=["none", "bicubic", "espcn"], default="none",
                    help="Upscale cosmetico opcional de CADA quadro antes de gravar "
                         "(ver upscale_quadros.py - EDSR/LapSRN ficaram de fora por nao "
                         "compensarem custo, ver CLAUDE.md). Padrao 'none' - comportamento "
                         "identico a antes.")
    ap.add_argument("--upscale-escala", type=int, default=2, choices=[2, 3, 4],
                    help="Fator de ampliacao se --upscale-metodo != none (padrao 2x).")
    ap.add_argument("--upscale-modelos-dir", default=None,
                    help="Pasta com ESPCN_x{2,3,4}.pb (padrao: modelos_sr/ do repo).")
    args = ap.parse_args()

    print("[gerar_quadros] versao: fix-memoria-incremental-2026-07-12")
    t_main = time.time()
    if not os.path.exists(args.video):
        print(f"Video nao encontrado: {args.video}")
        sys.exit(1)
    base_video = os.path.splitext(os.path.basename(args.video))[0]
    if args.out is None:
        args.out = f"quadros_{base_video}"
    os.makedirs(args.out, exist_ok=True)
    if args.miniaturas:
        os.makedirs(os.path.join(args.out, "mini"), exist_ok=True)

    t, P = carregar_trajetoria(args.trajetoria)
    alvos = alvos_por_distancia(t, P, args.intervalo, args.pausa_min)
    n_pausas = sum(1 for _, tp in alvos if tp == "pausa")
    print(f"Trajetoria: {t[-1]:.0f}s | alvos: {len(alvos)} "
          f"({len(alvos)-n_pausas} por percurso + {n_pausas} em pausas)")

    # Poses brutas do SLAM por frame (OPCIONAL) - permite anexar pose_raw a
    # cada quadro no manifest.json, pro super_resolucao.py reprojetar pontos
    # sem precisar do video bruto depois (ver carregar_poses_tum acima).
    poses_tum = None
    if args.traj_completa:
        if not os.path.exists(args.traj_completa):
            print(f"[AVISO] --traj-completa {args.traj_completa} nao encontrado - "
                  "quadros NAO terao pose_raw (super-resolucao por foto ficara "
                  "indisponivel nesta vistoria; panoramas normais seguem OK).")
        else:
            try:
                poses_tum = carregar_poses_tum(args.traj_completa)
            except ImportError:
                print("[AVISO] scipy nao instalado (pip install scipy) - quadros "
                      "NAO terao pose_raw; panoramas normais seguem OK.")
            if poses_tum is None:
                print(f"[AVISO] Nenhuma pose valida em {args.traj_completa} - "
                      "quadros sem pose_raw.")
            else:
                print(f"[SuperRes-prep] {len(poses_tum[0])} poses carregadas de "
                      f"--traj-completa pra anexar aos quadros (pose_raw).")

    # modo analise (ver help da flag): varre a copia reduzida, extrai so' os
    # escolhidos do original. Cai pro modo classico com aviso se a copia nao
    # servir (fps diferente = indices de frame nao mapeiam 1:1).
    video_varredura = args.video
    info_original = None
    if args.video_analise:
        if not os.path.exists(args.video_analise):
            print(f"[AVISO] --video-analise nao encontrado ({args.video_analise}) - "
                  "usando o video original (decode full-res, mais lento).")
        else:
            try:
                info_original = probe_video(args.video)
                fps_orig = info_original[0]
                fps_ana = probe_video(args.video_analise)[0]
                if abs(fps_orig - fps_ana) > 0.01 * max(fps_orig, 1e-9):
                    print(f"[AVISO] fps difere entre original ({fps_orig:.3f}) e analise "
                          f"({fps_ana:.3f}) - indices nao mapeiam; usando o original.")
                    info_original = None
                else:
                    video_varredura = args.video_analise
                    print(f"[Analise] Varredura de nitidez na copia reduzida "
                          f"({os.path.basename(args.video_analise)}); frames escolhidos "
                          "serao extraidos do original via seek.")
            except Exception as e:
                print(f"[AVISO] Falha ao inspecionar videos ({e}) - usando o original.")
                info_original = None

    t0 = time.time()
    cap = FFmpegVideoReader(video_varredura)
    if not cap.isOpened():
        print(f"Erro ao abrir o video: {video_varredura}")
        sys.exit(1)
    print(f"[TIMING] Abrir video (ffmpeg pipe): {time.time() - t0:.1f}s")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur_video = total / fps
    print(f"Video: {W}x{H} @ {fps:.2f} fps ({dur_video:.0f}s)")
    # escala temporal trajetoria->video (tolera pequenas discrepancias de relogio)
    esc_t = dur_video / max(t[-1], 1e-9)
    if abs(esc_t - 1.0) > 0.05:
        print(f"[AVISO] Duracao trajetoria ({t[-1]:.0f}s) != video ({dur_video:.0f}s); "
              f"reescalando tempos por {esc_t:.4f}")

    # janelas de frames por alvo
    janela_fr = max(1, int(round(args.janela * fps)))
    alvo_frames = []
    for tv, tipo in alvos:
        fc = int(round(tv * esc_t * fps))
        alvo_frames.append((max(0, fc - janela_fr), min(total - 1, fc + janela_fr), tv, tipo))

    # varredura unica do video: mantem o melhor frame por alvo
    #
    # IMPORTANTE (fix de memoria): so guardamos em RAM os frames dos alvos
    # cuja janela ainda esta "em aberto". Assim que a janela de um alvo
    # fecha (fidx ultrapassa o fim dela), gravamos o melhor frame no disco
    # NA HORA e liberamos a referencia. Antes desse fix, todos os frames
    # (ate 1 por alvo, em resolucoes altas tipo 5760x2880 ~50MB CADA) ficavam
    # acumulados ate o fim da varredura inteira, e um video de alguns minutos
    # com ~200+ alvos estourava a memoria (cv2.error: Insufficient memory).
    ext = args.formato
    par = ([int(cv2.IMWRITE_JPEG_QUALITY), args.qualidade] if ext == "jpg"
           else [int(cv2.IMWRITE_WEBP_QUALITY), args.qualidade])
    melhores = [None] * len(alvo_frames)  # (nitidez, frame_bgr, fidx) - so' os "em aberto"
    manifest = []
    tam_total = 0
    n_com_pose = 0
    tempo_upscale_total = 0.0
    tempo_extracao_total = 0.0
    n_extraidos = 0
    n_extracao_falhou = 0
    upscale_metodo = args.upscale_metodo if args.upscale_metodo != "none" else None
    if upscale_metodo:
        print(f"[Upscale] Ativado: metodo={upscale_metodo} escala={args.upscale_escala}x "
              f"(cosmetico - ver aviso no topo do arquivo/CLAUDE.md; nao recupera detalhe real)")

    def finalizar(k):
        """Grava (se houver frame) o alvo k no disco e libera a memoria dele."""
        nonlocal tam_total, n_com_pose, tempo_upscale_total, \
            tempo_extracao_total, n_extraidos, n_extracao_falhou
        best = melhores[k]
        melhores[k] = None
        if best is None:
            return
        sc, frame, fidx_best = best
        _, _, tv, tipo = alvo_frames[k]
        if info_original is not None:
            # modo analise: busca o frame full-res exato no video ORIGINAL.
            # +0.5 frame pra cair no MEIO do intervalo do frame alvo (evita
            # arredondar pro anterior na fronteira do seek).
            t_ext = time.time()
            ok_ext, frame_full = extrair_frame_no_tempo(
                args.video, (fidx_best + 0.5) / fps, info=info_original)
            tempo_extracao_total += time.time() - t_ext
            if ok_ext:
                frame = frame_full
                n_extraidos += 1
            else:
                n_extracao_falhou += 1
                print(f"[AVISO] Falha ao extrair frame full-res do alvo {k} "
                      f"(fidx={fidx_best}) - usando o frame da copia reduzida "
                      "(foto desse ponto fica em resolucao menor).")
        x = float(np.interp(tv, t, P[:, 0]))
        y = float(np.interp(tv, t, P[:, 1]))
        nome = f"quadro_{k:04d}.{ext}"
        # Miniatura sempre a partir do frame ORIGINAL (nao upscalado) - ela e'
        # reduzida de qualquer forma, upscalar antes so' desperdicaria tempo.
        if args.miniaturas:
            mini = cv2.resize(frame, (args.miniaturas, args.miniaturas * H // W),
                              interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(args.out, "mini", nome), mini, par)
        frame_gravar = frame
        if upscale_metodo:
            t_up = time.time()
            frame_gravar = _upscale_imagem_lazy(frame, upscale_metodo, args.upscale_escala,
                                                 args.upscale_modelos_dir)
            tempo_upscale_total += time.time() - t_up
        cv2.imwrite(os.path.join(args.out, nome), frame_gravar, par)
        entrada = dict(id=k, t=round(tv, 1), x=x, y=y,
                       arquivo=nome, tipo=tipo, nitidez=round(sc, 1))
        if poses_tum is not None:
            ts_poses, pos_w_poses, quat_wc_poses = poses_tum
            # tempo EXATO (video-native, segundos) do frame realmente escolhido -
            # nao o alvo tv da janela, que pode diferir ate +-args.janela dele.
            t_exato = fidx_best / fps
            idx = pose_mais_perto(ts_poses, t_exato)
            entrada["pose_raw"] = dict(
                pos_w=pos_w_poses[idx].tolist(),
                quat_wc=quat_wc_poses[idx].tolist(),
                dist_pose_s=round(float(abs(ts_poses[idx] - t_exato)), 3),
            )
            n_com_pose += 1
        manifest.append(entrada)
        tam_total += os.path.getsize(os.path.join(args.out, nome))

    prox = 0  # primeiro alvo cuja janela ainda nao terminou
    fidx = 0
    t_loop = time.time()
    while prox < len(alvo_frames):
        ret, frame = cap.read()
        if not ret:
            break
        # avanca e finaliza (grava + libera) alvos ja encerrados
        while prox < len(alvo_frames) and fidx > alvo_frames[prox][1]:
            finalizar(prox)
            prox += 1
        # este frame pertence a janela de algum alvo ativo?
        k = prox
        pertence = []
        while k < len(alvo_frames) and alvo_frames[k][0] <= fidx:
            if fidx <= alvo_frames[k][1]:
                pertence.append(k)
            k += 1
        if pertence:
            small = cv2.cvtColor(cv2.resize(frame, (640, 640 * H // W)), cv2.COLOR_BGR2GRAY)
            sc = nitidez(small)
            for k in pertence:
                if melhores[k] is None or sc > melhores[k][0]:
                    melhores[k] = (sc, frame.copy(), fidx)
        fidx += 1
        if fidx % 2000 == 0:
            decorrido = time.time() - t_loop
            taxa = fidx / decorrido if decorrido > 0 else 0
            eta = (total - fidx) / taxa if taxa > 0 else float("inf")
            print(f"  frame {fidx}/{total} | {decorrido:.0f}s decorridos | "
                  f"{taxa:.1f} frames/s decodificados | ETA {eta:.0f}s")
    cap.release()
    rotulo = ("decode da copia reduzida" if info_original is not None
              else "decode full-res")
    print(f"[TIMING] Varredura do video ({rotulo} + selecao de nitidez): "
          f"{time.time() - t_loop:.1f}s ({fidx} frames)")
    if info_original is not None:
        print(f"[TIMING] Extracao dos frames escolhidos do video original (seek): "
              f"{tempo_extracao_total:.1f}s ({n_extraidos} ok, {n_extracao_falhou} falhas)")
    # finaliza os alvos que restaram em aberto (janelas perto do fim do video)
    for k in range(prox, len(alvo_frames)):
        finalizar(k)

    manifest.sort(key=lambda m: m["id"])
    prefix = args.r2_prefix or base_video
    doc_manifest = dict(video=base_video, pasta=prefix,
                        gerado_em=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        total=len(manifest), quadros=manifest)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(doc_manifest, f, indent=1)
    print(f"\n[SUCESSO] {len(manifest)} panoramas em '{args.out}' "
          f"({tam_total/1e6:.0f} MB) + manifest.json")
    if poses_tum is not None:
        print(f"[SuperRes-prep] {n_com_pose}/{len(manifest)} quadros com pose_raw "
              "(super-resolucao por foto disponivel pra eles).")
    if upscale_metodo:
        media = tempo_upscale_total / max(len(manifest), 1)
        print(f"[TIMING] Upscale ({upscale_metodo} {args.upscale_escala}x): "
              f"{tempo_upscale_total:.1f}s total ({media:.2f}s/foto media)")

    if args.r2_bucket:
        subir_para_r2(args.out, args.r2_bucket, prefix, bool(args.miniaturas))

    print(f"[TIMING] gerar_quadros.py TOTAL: {time.time() - t_main:.1f}s")


if __name__ == "__main__":
    main()
