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
#   [--qualidade 82] [--miniaturas 1024]

import os
import sys
import json
import argparse
import mimetypes
import numpy as np

try:
    import cv2
except ImportError:
    print("Erro: instale as dependencias: pip install opencv-python numpy")
    sys.exit(1)


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

    arquivos = sorted(a for a in os.listdir(pasta_local)
                      if a != "manifest.json" and os.path.isfile(os.path.join(pasta_local, a)))
    print(f"[R2] Subindo {len(arquivos)} quadros para {bucket}/{prefix}/ ...")
    for i, a in enumerate(arquivos):
        put(os.path.join(pasta_local, a), f"{prefix}/{a}")
        if (i + 1) % 50 == 0:
            print(f"[R2]   {i+1}/{len(arquivos)}")
    if tem_miniaturas:
        minis = sorted(os.listdir(os.path.join(pasta_local, "mini")))
        print(f"[R2] Subindo {len(minis)} miniaturas...")
        for a in minis:
            put(os.path.join(pasta_local, "mini", a), f"{prefix}/mini/{a}")
    put(os.path.join(pasta_local, "manifest.json"), f"{prefix}/manifest.json")
    print(f"[R2] Concluido. Manifest em: {prefix}/manifest.json")


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
    ap.add_argument("--formato", choices=["jpg", "webp"], default="jpg")
    ap.add_argument("--qualidade", type=int, default=82)
    ap.add_argument("--miniaturas", type=int, default=0,
                    help="Se >0, gera tambem miniaturas com esta largura (px)")
    ap.add_argument("--r2-bucket", default=None,
                    help="Bucket do Cloudflare R2 para upload automatico. Credenciais via "
                         "variaveis de ambiente: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                         "R2_SECRET_ACCESS_KEY (nunca no codigo).")
    ap.add_argument("--r2-prefix", default=None,
                    help="Pasta no bucket (padrao: nome do arquivo de video)")
    args = ap.parse_args()

    print("[gerar_quadros] versao: fix-memoria-incremental-2026-07-12")
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

    cap = cv2.VideoCapture(args.video)
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
    melhores = [None] * len(alvo_frames)  # (nitidez, frame_bgr) - so' os "em aberto"
    manifest = []
    tam_total = 0

    def finalizar(k):
        """Grava (se houver frame) o alvo k no disco e libera a memoria dele."""
        nonlocal tam_total
        best = melhores[k]
        melhores[k] = None
        if best is None:
            return
        sc, frame = best
        _, _, tv, tipo = alvo_frames[k]
        x = float(np.interp(tv, t, P[:, 0]))
        y = float(np.interp(tv, t, P[:, 1]))
        nome = f"quadro_{k:04d}.{ext}"
        cv2.imwrite(os.path.join(args.out, nome), frame, par)
        if args.miniaturas:
            mini = cv2.resize(frame, (args.miniaturas, args.miniaturas * H // W),
                              interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(args.out, "mini", nome), mini, par)
        manifest.append(dict(id=k, t=round(tv, 1), x=x, y=y,
                             arquivo=nome, tipo=tipo, nitidez=round(sc, 1)))
        tam_total += os.path.getsize(os.path.join(args.out, nome))

    prox = 0  # primeiro alvo cuja janela ainda nao terminou
    fidx = 0
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
                    melhores[k] = (sc, frame.copy())
        fidx += 1
        if fidx % 2000 == 0:
            print(f"  frame {fidx}/{total}")
    cap.release()
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

    if args.r2_bucket:
        subir_para_r2(args.out, args.r2_bucket, prefix, bool(args.miniaturas))


if __name__ == "__main__":
    main()
