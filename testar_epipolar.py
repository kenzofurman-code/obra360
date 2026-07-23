#!/usr/bin/env python3
"""
Testa o fallback epipolar/fotometrico (medir_por_epipolar_fallback) com dados
REAIS - roda na maquina do Pedro (o sandbox do Cowork so' tem 2 quadros muito
distantes entre si, baseline 11.7u, impróprio pra casamento fotometrico).

PRE-REQUISITO CRITICO: o par de quadros precisa ter BASELINE PEQUENO (~0.5-2
unid SLAM = quadros proximos no tempo/percurso) e SOBREPOSICAO alta. Quadros de
pontas opostas da caminhada NAO funcionam (a mesma parede vista de angulos muito
diferentes fica irreconhecivel - NCC ~0).

Uso 1 - VALIDAR contra landmarks co-visiveis (gabarito 3D): pega N landmarks
vistos nos 2 quadros, mede cada um pelo epipolar e compara com a profundidade
verdadeira do landmark.

    python testar_epipolar.py --mapa mapa.msg \
        --frame1 quadro_0100.jpg --t1 55.0 \
        --frame2 quadro_0110.jpg --t2 60.0 --validar 8

Uso 2 - MEDIR um clique especifico (u,v em [0,1]):

    python testar_epipolar.py --mapa mapa.msg \
        --frame1 quadro_0100.jpg --t1 55.0 \
        --frame2 quadro_0110.jpg --t2 60.0 --clique 0.52,0.55

Dica pra achar um bom par: dois quadros consecutivos do manifest.json costumam
ter baseline pequeno. Veja o campo x,y de cada quadro - a distancia entre eles
e' o baseline (queira algo entre ~0.5 e ~2).
"""
import argparse, numpy as np, cv2
import medir_panorama as mp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mapa', required=True)
    ap.add_argument('--frame1', required=True); ap.add_argument('--t1', type=float, required=True)
    ap.add_argument('--frame2', required=True); ap.add_argument('--t2', type=float, required=True)
    ap.add_argument('--validar', type=int, default=0, help='N landmarks co-visiveis pra validar')
    ap.add_argument('--clique', default=None, help='u,v em [0,1] pra medir um ponto so')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()

    keyframes, _, landmarks = mp.carregar_mapa(a.mapa)
    kf1 = mp.pose_no_frame_do_mapa(keyframes, a.t1)
    kf2 = mp.pose_no_frame_do_mapa(keyframes, a.t2)
    if kf1 is None or kf2 is None:
        print('ERRO: sem keyframe do mapa perto de t1/t2.'); return
    base = float(np.linalg.norm(kf1['pos_w'] - kf2['pos_w']))
    print(f'baseline entre os 2 quadros: {base:.2f} unid SLAM', end='')
    print('  (BOM)' if 0.3 < base < 3 else '  (RUIM - queira ~0.5-2; longe demais nao casa)')
    img1 = cv2.imread(a.frame1); img2 = cv2.imread(a.frame2)
    if img1 is None or img2 is None:
        print('ERRO: nao consegui abrir frame1/frame2.'); return

    if a.clique:
        u, v = [float(x) for x in a.clique.split(',')]
        r = mp.medir_por_epipolar_fallback(kf1, u, v, img1, kf2, img2)
        if r['sucesso']:
            print(f"clique ({u},{v}): profundidade={r['profundidade']:.2f} unid, "
                  f"NCC={r['ncc']:.2f} ({r['confianca']}), ponto3d={np.round(r['ponto3d'],3)}")
        else:
            print(f"clique ({u},{v}): FALHOU - {r['motivo']}")
        return

    n = a.validar or 6
    u1, v1, d1 = mp.reprojetar_landmarks(kf1, landmarks)
    u2, v2, d2 = mp.reprojetar_landmarks(kf2, landmarks)
    du1 = np.minimum(np.abs(u1 - .5), 1 - np.abs(u1 - .5))
    du2 = np.minimum(np.abs(u2 - .5), 1 - np.abs(u2 - .5))
    covis = ((du1 < 50/360.) & (np.abs(v1-.5) < 30/180.) &
             (du2 < 50/360.) & (np.abs(v2-.5) < 30/180.) & (d1 > 1) & (d1 < 20))
    idx = np.where(covis)[0]
    if len(idx) == 0:
        print('Nenhum landmark co-visivel no centro dos 2 quadros - sobreposicao baixa demais.'); return
    np.random.seed(a.seed); idx = np.random.choice(idx, min(n, len(idx)), replace=False)
    print(f'validando {len(idx)} landmarks co-visiveis (gabarito = profundidade real do landmark):')
    erros = []
    for k in idx:
        L = landmarks[k]; dv = float(np.linalg.norm(L - kf1['pos_w']))
        r = mp.medir_por_epipolar_fallback(kf1, float(u1[k]), float(v1[k]), img1, kf2, img2)
        if r['sucesso']:
            err = abs(r['profundidade'] - dv) / dv * 100; erros.append(err)
            print(f"  real={dv:5.2f}  epipolar={r['profundidade']:5.2f}  erro={err:5.1f}%  NCC={r['ncc']:.2f}")
        else:
            print(f"  real={dv:5.2f}  FALHOU (NCC={r['ncc']:.2f})")
    if erros:
        print(f'erro mediano: {np.median(erros):.1f}%  |  sucessos: {len(erros)}/{len(idx)}')
    else:
        print('nenhum sucesso - confira o baseline (provavelmente longe demais) e a sobreposicao.')


if __name__ == '__main__':
    main()
