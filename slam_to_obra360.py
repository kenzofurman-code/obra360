# -*- coding: utf-8 -*-
# slam_to_obra360.py
# Converte a trajetoria do stella_vslam (frame_trajectory.txt, formato TUM)
# para o JSON do Obra360 [{t, x, y}, ...], com projecao para o plano da planta,
# ancoragem inicio/fim e comparacao opcional com um gabarito.
#
# Formato TUM: timestamp tx ty tz qx qy qz qw   (uma linha por frame rastreado)
#
# Uso basico:
#   python slam_to_obra360.py --traj frame_trajectory.txt --out caminho.json \
#       --inicio 0.42,0.57 --fim 0.57,0.94
#
# Com gabarito (auto-detecta espelhamento e reporta o erro):
#   python slam_to_obra360.py --traj frame_trajectory.txt --out caminho.json \
#       --inicio 0.42,0.57 --fim 0.57,0.94 --gabarito gabarito.json

import json
import argparse
import sys
import numpy as np


def load_tum(path):
    ts, pos = [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            ts.append(float(parts[0]))
            pos.append([float(parts[1]), float(parts[2]), float(parts[3])])
    if not ts:
        return None, None
    ts = np.array(ts)
    pos = np.array(pos)
    order = np.argsort(ts)
    ts, pos = ts[order], pos[order]
    # remove timestamps duplicados
    keep = np.concatenate([[True], np.diff(ts) > 1e-9])
    return ts[keep], pos[keep]


def project_to_plan(pos3d, mode="xz"):
    """
    Projeta a trajetoria 3D no plano da planta.
      xz  : descarta o eixo Y (convencao do stella_vslam: Y ~ vertical se a
            camera estava aproximadamente nivelada no inicio do video)
      pca : usa os 2 eixos de maior variancia (fallback para camera inclinada)
    """
    if mode == "pca":
        c = pos3d - pos3d.mean(0)
        _, _, Vt = np.linalg.svd(c, full_matrices=False)
        P = c @ Vt[:2].T
        return P
    # modo xz
    return np.stack([pos3d[:, 0], pos3d[:, 2]], axis=1)


def resample_time(ts, P, rate):
    t0 = ts[0]
    t = ts - t0
    t_out = np.arange(0.0, t[-1] + 1e-9, rate)
    x = np.interp(t_out, t, P[:, 0])
    y = np.interp(t_out, t, P[:, 1])
    return t_out, np.stack([x, y], axis=1)


def similarity_from_two_points(a, b, A, B):
    """Rotacao+escala+translacao que leva a->A e b->B."""
    v_src, v_dst = b - a, B - A
    n_src = np.linalg.norm(v_src)
    if n_src < 1e-12:
        return None
    s = np.linalg.norm(v_dst) / n_src
    ang = np.arctan2(v_dst[1], v_dst[0]) - np.arctan2(v_src[1], v_src[0])
    c, sn = np.cos(ang), np.sin(ang)
    R = np.array([[c, -sn], [sn, c]])
    return lambda P: (s * (R @ (P - a).T)).T + A


def arc_length(P):
    d = np.linalg.norm(np.diff(P, axis=0), axis=1)
    return np.concatenate([[0], np.cumsum(d)])


def resample_arc(P, n):
    s = arc_length(P)
    if s[-1] == 0:
        return np.repeat(P[:1], n, axis=0)
    u = np.linspace(0, s[-1], n)
    return np.stack([np.interp(u, s, P[:, 0]), np.interp(u, s, P[:, 1])], axis=1)


def umeyama(src, dst):
    ms, md = src.mean(0), dst.mean(0)
    S, D = src - ms, dst - md
    C = D.T @ S / len(src)
    U, Sig, Vt = np.linalg.svd(C)
    d = np.sign(np.linalg.det(U @ Vt))
    E = np.diag([1, d])
    R = U @ E @ Vt
    c = np.trace(np.diag(Sig) @ E) / ((S ** 2).sum() / len(src))
    return lambda P: (c * (R @ (P - ms).T)).T + md


def shape_error(P, ref, n=200):
    Pn, Rn = resample_arc(P, n), resample_arc(ref, n)
    T = umeyama(Pn, Rn)
    return float(np.linalg.norm(T(Pn) - Rn, axis=1).mean())


def time_error(t, P, gab_t, gab_xy):
    """Erro apos melhor alinhamento, comparando pontos no MESMO instante."""
    Pi = np.stack([np.interp(gab_t, t, P[:, 0]), np.interp(gab_t, t, P[:, 1])], axis=1)
    T = umeyama(Pi, gab_xy)
    return float(np.linalg.norm(T(Pi) - gab_xy, axis=1).mean())


def door_quality_and_correction(t_out, P_out, portas_path, aplicar=True, asp=1.0):
    """
    Valida a trajetoria contra os vaos de porta extraidos do PDF vetorial
    (extrair_portas.py) e aplica correcao suave APENAS se ela reduzir os
    residuais nas portas de validacao (metade e' reservada para teste).
    Imprime um relatorio de qualidade: portas atravessadas e residual.
    """
    with open(portas_path, "r", encoding="utf-8") as f:
        vaos = json.load(f)
    if isinstance(vaos, dict):
        vaos = vaos["vaos"]
    # trabalha em coordenadas fisicas (corrige anisotropia do normalizado)
    A = np.array([1.0, asp])
    vaos = [dict(v, hinge=(np.array(v["hinge"])*A).tolist(),
                 extremos=[(np.array(e)*A).tolist() for e in v["extremos"]]) for v in vaos]
    P_out = P_out * A

    def seg_intersect(p1, p2, q1, q2):
        d1, d2 = p2-p1, q2-q1
        den = d1[0]*d2[1]-d1[1]*d2[0]
        if abs(den) < 1e-12:
            return None
        s = ((q1[0]-p1[0])*d2[1]-(q1[1]-p1[1])*d2[0])/den
        u = ((q1[0]-p1[0])*d1[1]-(q1[1]-p1[1])*d1[0])/den
        return s if (0 <= s <= 1 and 0 <= u <= 1) else None

    def find_crossings(X):
        out = []
        for v in vaos:
            h = np.array(v["hinge"])
            best = None
            for e in map(np.array, v["extremos"]):
                centro = (h+e)/2
                largura = np.linalg.norm(e-h)
                dd = np.hypot(X[:, 0]-centro[0], X[:, 1]-centro[1])
                for j in np.where(dd < 0.03 + largura)[0]:
                    if j+1 >= len(X):
                        continue
                    ext = 0.3*(e-h)
                    s = seg_intersect(X[j], X[j+1], h-ext, e+ext)
                    if s is not None:
                        ptraj = X[j] + s*(X[j+1]-X[j])
                        r = centro - ptraj
                        tc = t_out[j] + s*(t_out[min(j+1, len(t_out)-1)]-t_out[j])
                        cand = (float(np.linalg.norm(r)), tc, r, v["nome"])
                        if best is None or cand[0] < best[0]:
                            best = cand
            if best:
                out.append(best)
        return out

    cross = find_crossings(P_out)
    if not cross:
        print("[PORTAS] Nenhum cruzamento de porta detectado (alinhamento ruim ou PDF de outro pavimento?).")
        return P_out / A
    res = np.array([c[0] for c in cross])
    print(f"[PORTAS] {len(cross)}/{len(vaos)} portas atravessadas | residual: "
          f"mediana={np.median(res):.4f} max={res.max():.4f}")

    if not aplicar or len(cross) < 6:
        return P_out / A

    # correcao suave gateada por holdout nas proprias portas
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(cross))
    fit = [cross[i] for i in perm[:len(cross)//2]]
    val_names = {cross[i][3] for i in perm[len(cross)//2:]}

    def apply_corr(X, cs, sigma_t=30.0, max_corr=0.04):
        tk = np.array([c[1] for c in cs])
        rk = np.array([c[2] for c in cs])
        Wg = np.exp(-0.5*((t_out[:, None]-tk[None, :])/sigma_t)**2)
        Ws = Wg.sum(1, keepdims=True)
        delta = (Wg @ rk)/np.maximum(Ws, 1e-9)
        return X + np.clip(delta, -max_corr, max_corr)*np.clip(Ws/0.4, 0, 1)

    Xc = apply_corr(P_out, fit)
    val_before = [c[0] for c in cross if c[3] in val_names]
    val_after = [c[0] for c in find_crossings(Xc) if c[3] in val_names]
    if val_after and np.median(val_after) < np.median(val_before) * 0.85:
        print(f"[PORTAS] Correcao aplicada: residual de validacao "
              f"{np.median(val_before):.4f} -> {np.median(val_after):.4f}")
        return apply_corr(P_out, cross) / A
    print("[PORTAS] Trajetoria ja consistente com os vaos; correcao desnecessaria.")
    return P_out / A


def parse_xy(s):
    parts = s.replace(";", ",").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Use o formato x,y  (ex.: 0.42,0.57)")
    return np.array([float(parts[0]), float(parts[1])])


def align_by_references(t, P, refs, n_icp=25):
    """
    Alinha a trajetoria a pontos de referencia [{t,x,y},...] marcados na planta.
      1. Alinhamento espacial inicial (Umeyama) usando o casamento temporal cru.
      2. Refinamento: cada referencia e' associada ao ponto mais proximo da
         trajetoria dentro de uma janela temporal, alternando com re-alinhamento.
      3. Calibra o TEMPO: ajusta um mapa monotonico por trechos (indice -> t)
         a partir das associacoes, corrigindo fps errado, VFR e offset de
         inicializacao.
    Retorna (t_corrigido, P_alinhado, relatorio).
    """
    Rt = np.array([r["t"] for r in refs], float)
    R = np.array([[r["x"], r["y"]] for r in refs], float)
    order = np.argsort(Rt)
    Rt, R = Rt[order], R[order]

    def interp(tq, tt, X):
        return np.stack([np.interp(tq, tt, X[:, 0]), np.interp(tq, tt, X[:, 1])], 1)

    def ref_err(tt, X):
        return np.linalg.norm(interp(Rt, tt, X) - R, axis=1).mean()

    # ---- Etapa 1: alinhamento TEMPORAL direto (offset + Umeyama, com espelho) ----
    span = t[-1] - t[0]
    best = None
    for t0 in np.arange(0.0, min(15.0, 0.1 * span) + 1e-9, 0.5):
        tt = t - t[0] + t0
        for Px in (P, P * np.array([1, -1])):
            Gi = interp(Rt, tt, Px)
            T = umeyama(Gi, R)
            e = np.linalg.norm(T(Gi) - R, axis=1).mean()
            if best is None or e < best[0]:
                best = (e, tt.copy(), T(Px))
    e_direct, tt, X = best

    # ---- Etapa 2: refinamento com recalibracao de tempo (so se melhorar) ----
    tt2, X2 = tt.copy(), X.copy()

    idx_match = None
    for it in range(n_icp):
        # janela temporal moderada: o tempo direto ja e' um bom chute
        frac = max(0.12 * (1 - it / n_icp), 0.04)
        win = frac * (tt2[-1] - tt2[0])
        idx_match = []
        for k in range(len(R)):
            sel = np.where(np.abs(tt2 - Rt[k]) <= win)[0]
            if len(sel) == 0:
                sel = np.arange(len(tt2))
            j = sel[np.argmin(np.linalg.norm(X2[sel] - R[k], axis=1))]
            idx_match.append(j)
        idx_match = np.array(idx_match)
        T = umeyama(X2[idx_match], R)
        X2 = T(X2)
        tm = tt2[idx_match]
        mono = np.maximum.accumulate(tm)
        tt2 = np.interp(tt2, np.concatenate([[tt2[0]], mono, [tt2[-1]]]),
                        np.concatenate([[min(Rt[0], tt2[0])], Rt, [max(Rt[-1], tt2[-1])]]))

    e_refined = ref_err(tt2, X2)
    if e_refined < e_direct:
        tt, X, e_final = tt2, X2, e_refined
        modo = "refinado (tempo recalibrado)"
    else:
        e_final = e_direct
        modo = "temporal direto"

    err = np.linalg.norm(interp(Rt, tt, X) - R, axis=1)
    rel = {"erro_medio_refs": float(err.mean()), "erro_max_refs": float(err.max()),
           "n_refs": len(R), "modo": modo}
    return tt, X, rel


def main():
    ap = argparse.ArgumentParser(description="stella_vslam (TUM) -> Obra360 JSON")
    ap.add_argument("--traj", required=True, help="frame_trajectory.txt gerado pelo stella_vslam")
    ap.add_argument("--out", default="caminho_vistoria.json")
    ap.add_argument("--rate", type=float, default=0.5, help="amostragem em segundos (padrao 0.5)")
    ap.add_argument("--inicio", type=parse_xy, default=None, help="ponto inicial na planta: x,y")
    ap.add_argument("--fim", type=parse_xy, default=None, help="ponto final na planta: x,y")
    ap.add_argument("--proj", choices=["xz", "pca"], default="xz",
                    help="projecao 3D->2D (padrao xz; use pca se a camera iniciou inclinada)")
    ap.add_argument("--espelhar", action="store_true",
                    help="espelha o eixo y antes da ancoragem (se o caminho sair invertido)")
    ap.add_argument("--aspecto", type=float, default=None,
                    help="Razao altura/largura da pagina do PDF da planta. Corrige a "
                         "anisotropia das coordenadas normalizadas (auto-detectado do "
                         "arquivo de --portas quando disponivel).")
    ap.add_argument("--portas", default=None,
                    help="JSON de portas do PDF vetorial (extrair_portas.py) para "
                         "relatorio de qualidade e correcao fina opcional.")
    ap.add_argument("--referencia", default=None,
                    help="JSON de pontos de referencia [{t,x,y},...] marcados na planta. "
                         "Substitui --inicio/--fim: alinha por minimos quadrados e "
                         "CALIBRA O TEMPO da trajetoria (recomendado: 5-10 pontos).")
    ap.add_argument("--gabarito", default=None,
                    help="JSON de pontos reais [{x,y},...] para avaliar e auto-detectar espelhamento")
    args = ap.parse_args()

    ts, pos3d = load_tum(args.traj)
    if ts is None:
        print(f"Nenhuma pose encontrada em {args.traj}")
        sys.exit(1)

    dur = ts[-1] - ts[0]
    print(f"Trajetoria SLAM: {len(ts)} poses, {dur:.1f}s")
    if len(ts) < 10:
        print("[AVISO] Muito poucas poses — o rastreamento provavelmente falhou.")

    P = project_to_plan(pos3d, args.proj)
    vert_var = np.var(pos3d[:, 1])
    plan_var = np.var(P).sum()
    if args.proj == "xz" and vert_var > 0.15 * plan_var:
        print("[AVISO] Variancia vertical alta em relacao ao plano — se o resultado "
              "sair distorcido, tente --proj pca (ou verifique se ha escadas no percurso).")

    # ----- Modo REFERENCIA: alinhamento + calibracao de tempo por pontos -----
    if args.referencia:
        with open(args.referencia, "r", encoding="utf-8") as f:
            refs = json.load(f)
        refs = [r for r in refs if "t" in r]
        # aspecto da pagina: flag > arquivo de portas > 1.0
        asp = args.aspecto
        vaos_data = None
        if args.portas:
            with open(args.portas, "r", encoding="utf-8") as f:
                vaos_data = json.load(f)
            if asp is None and isinstance(vaos_data, dict):
                asp = vaos_data["pagina"]["aspecto"]
        if asp is None:
            asp = 1.0
            print("[AVISO] Aspecto da pagina desconhecido (use --aspecto ou --portas). "
                  "Coordenadas normalizadas anisotropicas degradam a precisao.")
        else:
            print(f"Correcao de aspecto da pagina: y x{asp:.4f} (alinhamento em coords fisicas)")
        refs = [dict(r, y=r["y"] * asp) for r in refs]
        if len(refs) < 3:
            print("[ERRO] --referencia precisa de pelo menos 3 pontos com 't'.")
            sys.exit(1)
        t_rel = ts - ts[0]
        t_cal, X, rel = align_by_references(t_rel, P, refs)
        print(f"Alinhado por {rel['n_refs']} referencias [{rel['modo']}]: erro nas refs "
              f"medio={rel['erro_medio_refs']:.4f} max={rel['erro_max_refs']:.4f}")
        t_out = np.arange(0.0, t_cal[-1] + 1e-9, args.rate)
        P_out = np.stack([np.interp(t_out, t_cal, X[:, 0]),
                          np.interp(t_out, t_cal, X[:, 1])], 1)
        P_out[:, 1] /= asp  # de volta ao normalizado do Obra360
        if args.portas:
            P_out = door_quality_and_correction(t_out, P_out, args.portas, asp=asp)
        waypoints = [{"t": round(float(tt), 1), "x": float(p[0]), "y": float(p[1])}
                     for tt, p in zip(t_out, P_out)]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(waypoints, f, indent=2)
        print(f"Trajetoria salva em: {args.out}")
        return

    if args.espelhar:
        P = P * np.array([1, -1])

    # Auto-deteccao de espelhamento via gabarito (temporal se houver 't', senao por forma)
    gab = None
    gab_t = None
    if args.gabarito:
        with open(args.gabarito, "r", encoding="utf-8") as f:
            gab_raw = json.load(f)
        gab = np.array([[p["x"], p["y"]] for p in gab_raw])
        if all("t" in p for p in gab_raw):
            gab_t = np.array([float(p["t"]) for p in gab_raw])
            t_rel = ts - ts[0]
            e_norm = time_error(t_rel, P, gab_t, gab)
            e_flip = time_error(t_rel, P * np.array([1, -1]), gab_t, gab)
            print(f"Erro TEMPORAL vs gabarito: normal={e_norm:.4f}  espelhado={e_flip:.4f}")
        else:
            e_norm = shape_error(P, gab)
            e_flip = shape_error(P * np.array([1, -1]), gab)
            print(f"Erro de forma vs gabarito: normal={e_norm:.4f}  espelhado={e_flip:.4f}")
        if e_flip < e_norm:
            print("-> Aplicando espelhamento automaticamente (melhor encaixe com o gabarito).")
            P = P * np.array([1, -1])

    t_out, P_out = resample_time(ts, P, args.rate)

    # Ancoragem na planta ou normalizacao
    if args.inicio is not None and args.fim is not None:
        T = similarity_from_two_points(P_out[0], P_out[-1], args.inicio, args.fim)
        if T is None:
            print("[AVISO] Inicio e fim coincidem; impossivel ancorar. Normalizando.")
            k = 2.0 / max(np.linalg.norm(P_out, axis=1).max(), 1e-9)
            P_out = P_out * k
        else:
            P_out = T(P_out)
            print(f"Ancorado: inicio=({args.inicio[0]:.4f}, {args.inicio[1]:.4f}) "
                  f"fim=({args.fim[0]:.4f}, {args.fim[1]:.4f})")
    else:
        P_out = P_out - P_out[0]
        k = 2.0 / max(np.linalg.norm(P_out, axis=1).max(), 1e-9)
        P_out = P_out * k
        print("[DICA] Informe --inicio x,y e --fim x,y para cair direto nas coordenadas da planta.")

    if gab is not None:
        if gab_t is not None:
            Gi = np.stack([np.interp(gab_t, t_out, P_out[:, 0]),
                           np.interp(gab_t, t_out, P_out[:, 1])], axis=1)
            err = np.linalg.norm(Gi - gab, axis=1)
            print(f"Erro final TEMPORAL vs gabarito (coordenadas da planta): "
                  f"medio={err.mean():.4f}  max={err.max():.4f}")
        else:
            n = 200
            err = np.linalg.norm(resample_arc(P_out, n) - resample_arc(gab, n), axis=1)
            print(f"Erro final vs gabarito (coordenadas da planta): "
                  f"medio={err.mean():.4f}  max={err.max():.4f}")

    waypoints = [{"t": round(float(t), 1), "x": float(p[0]), "y": float(p[1])}
                 for t, p in zip(t_out, P_out)]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(waypoints, f, indent=2)
    print(f"Trajetoria salva em: {args.out}")


if __name__ == "__main__":
    main()
