# -*- coding: utf-8 -*-
# retrofit_pose_raw.py
# Atalho pra NAO precisar reprocessar a vistoria inteira (~76min neste video
# de 46.8GB) so' pra testar super_resolucao.py: anexa pose_raw num
# manifest.json JA GERADO (de um run feito ANTES do --traj-completa
# funcionar, ex.: por falta de scipy instalado), usando o campo "t" de cada
# quadro (o ALVO da amostragem, nao o frame EXATO escolhido - imprecisao de
# ate +-janela, 0.5s por padrao do gerar_quadros.py) casado contra o
# frame_trajectory.txt (ja salvo pelo rodar_slam.py, nao apagado).
#
# Uso pontual/teste - o fluxo normal (worker.py -> gerar_quadros.py
# --traj-completa, ja rodando automaticamente) faz isso com precisao EXATA
# (casa pelo fidx do frame realmente escolhido, nao pelo alvo). Prefira
# reprocessar do zero quando puder esperar - isso aqui e' so' pra validar o
# CONCEITO de super_resolucao.py sem esperar +76min de novo.
#
# Uso:
#   pip install scipy
#   python retrofit_pose_raw.py --quadros <pasta_quadros> --traj-completa frame_trajectory.txt

import argparse
import json
import os

from gerar_quadros import carregar_poses_tum, pose_mais_perto


def main():
    ap = argparse.ArgumentParser(
        description="Anexa pose_raw a um manifest.json ja gerado (sem reprocessar a vistoria).")
    ap.add_argument("--quadros", required=True, help="Pasta com manifest.json + fotos ja geradas.")
    ap.add_argument("--traj-completa", required=True, help="frame_trajectory.txt do stella_vslam.")
    ap.add_argument("--forcar", action="store_true",
                    help="Recalcula pose_raw mesmo pros quadros que ja tem (padrao: so' preenche os que faltam).")
    args = ap.parse_args()

    manifest_path = os.path.join(args.quadros, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    poses = carregar_poses_tum(args.traj_completa)
    if poses is None:
        raise SystemExit(f"Nenhuma pose valida em {args.traj_completa}")
    ts_poses, pos_w_poses, quat_wc_poses = poses
    print(f"[Retrofit] {len(ts_poses)} poses carregadas de {args.traj_completa}")

    n = 0
    dists = []
    for q in doc["quadros"]:
        if q.get("pose_raw") and not args.forcar:
            continue
        idx = pose_mais_perto(ts_poses, q["t"])
        dist_pose_s = round(float(abs(ts_poses[idx] - q["t"])), 3)
        q["pose_raw"] = dict(
            pos_w=pos_w_poses[idx].tolist(),
            quat_wc=quat_wc_poses[idx].tolist(),
            dist_pose_s=dist_pose_s,
            retrofit=True,  # marca que veio deste atalho (casado pelo "t" alvo, nao pelo
                            # frame exato escolhido - diferente do fluxo normal via worker.py)
        )
        dists.append(dist_pose_s)
        n += 1

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)

    print(f"[Retrofit] {n} quadro(s) ganharam pose_raw.")
    if dists:
        print(f"[Retrofit] dist_pose_s: media={sum(dists)/len(dists):.3f}s  "
              f"max={max(dists):.3f}s (quanto menor, melhor o casamento de tempo)")
    print(f"[Retrofit] manifest.json atualizado em: {manifest_path}")
    print("[Retrofit] LEMBRETE: se este manifest ja foi upado pro R2, suba a versao "
          "atualizada de novo (ou aponte super_resolucao.py pra esta pasta local).")


if __name__ == "__main__":
    main()
