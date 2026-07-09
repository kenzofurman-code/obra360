# map_matcher.py
import json
import math
import os
import numpy as np

# Caminhos dos arquivos
proj_dir = r"c:\Users\HomePC\.gemini\antigravity-ide\scratch\video360-obras-app"
gabarito_path = r"C:\Users\HomePC\Desktop\gabarito_trajetoria_6°_pavimento (1).json"
raw_path = os.path.join(proj_dir, "VID_20260703_110303_00_021_caminho.json")
passagens_path = os.path.join(proj_dir, "planta_passagens.json")
output_path = r"C:\Users\HomePC\Desktop\VID_20260703_110303_00_021_corrigido.json"

TIME_OFFSET = 35.5  # Pré-caminhada no vídeo de alta resolução
SNAP_THRESHOLD = 0.04  # Raio máximo para detecção de passagem de porta (4% do mapa, ~1.2 metros)

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_closest_raw(raw_list, t_target):
    best_pt = None
    min_diff = float('inf')
    for pt in raw_list:
        diff = abs(pt['t'] - t_target)
        if diff < min_diff:
            min_diff = diff
            best_pt = pt
    return best_pt

def main():
    if not all(os.path.exists(p) for p in [gabarito_path, raw_path, passagens_path]):
        print("[ERRO] Arquivos de entrada necessários não encontrados.")
        return

    gabarito = load_json(gabarito_path)
    raw = load_json(raw_path)
    passagens = load_json(passagens_path)

    print(f"Gabarito: {len(gabarito)} pontos de controle.")
    print(f"Odometria bruta EKF: {len(raw)} pontos.")
    print(f"Passagens (Portas PDF): {len(passagens)} vãos carregados.")

    # --- PASSO 1: ALINHAMENTO PROCRUSTES SVD ---
    # Espelha horizontalmente as coordenadas brutas do vídeo para alinhar quiralidade
    for pt in raw:
        pt['x'] = -pt['x']

    g_pts = []
    r_pts = []
    for g_pt in sorted(gabarito, key=lambda x: x['t']):
        t = g_pt['t']
        r_pt = find_closest_raw(raw, t + TIME_OFFSET)
        if r_pt:
            g_pts.append([g_pt['x'], g_pt['y']])
            r_pts.append([r_pt['x'], r_pt['y']])

    G = np.array(g_pts)
    R = np.array(r_pts)

    G_mean = np.mean(G, axis=0)
    R_mean = np.mean(R, axis=0)
    Gc = G - G_mean
    Rc = R - R_mean

    H = np.dot(Rc.T, Gc)
    U, S, Vt = np.linalg.svd(H)
    R_mat = np.dot(U, Vt)

    # Garante rotação pura (sem reflexão na matriz de rotação pós-espelhamento)
    if np.linalg.det(R_mat) < 0:
        Vt[1, :] *= -1
        R_mat = np.dot(U, Vt)

    var_R = np.sum(Rc ** 2)
    s = np.trace(np.dot(np.dot(Rc, R_mat).T, Gc)) / var_R if var_R > 0 else 1.0

    # Aplica alinhamento inicial nos pontos
    raw_aligned = []
    for pt in raw:
        pt_raw = np.array([pt['x'], pt['y']])
        pt_centered = pt_raw - R_mean
        pt_aligned = np.dot(pt_centered, R_mat) * s + G_mean
        raw_aligned.append({
            "t": pt['t'],
            "x": float(pt_aligned[0]),
            "y": float(pt_aligned[1])
        })

    # --- PASSO 2: DETECÇÃO DE EVENTOS DE PASSAGEM DE PORTA ---
    # Para cada porta do PDF, procuramos qual ponto da trajetória alinhada passou mais perto
    passagens_detectadas = [] # lista de tuples: (t_video, porta_dict, dist)
    
    for gate in passagens:
        gate_x = gate['x_norm']
        gate_y = gate['y_norm']
        
        best_t = None
        best_dist = float('inf')
        
        # Ignora os primeiros 35.5s de preparação do vídeo para busca de portas
        for pt in raw_aligned:
            if pt['t'] < TIME_OFFSET:
                continue
                
            dist = math.sqrt((pt['x'] - gate_x) ** 2 + (pt['y'] - gate_y) ** 2)
            if dist < best_dist:
                best_dist = dist
                best_t = pt['t']
                
        # Se passou abaixo do limiar (SNAP_THRESHOLD), registramos a passagem
        if best_dist < SNAP_THRESHOLD:
            passagens_detectadas.append({
                "t": best_t,
                "gate": gate,
                "dist": best_dist
            })

    # Filtra passagens duplicadas em tempo ou portas muito próximas, ordenando cronologicamente
    passagens_detectadas = sorted(passagens_detectadas, key=lambda x: x['t'])
    
    # Remove passagens que estejam muito próximas temporalmente (evita falsos positivos oscilando)
    filtro_passagens = []
    for p in passagens_detectadas:
        if not filtro_passagens or (p['t'] - filtro_passagens[-1]['t']) > 4.0: # intervalo mín de 4s entre portas
            filtro_passagens.append(p)
            
    print(f"\nPortas detectadas e sincronizadas no vídeo: {len(filtro_passagens)}")
    for p in filtro_passagens:
        print(f"t_video: {p['t']:>5.1f}s | Código: {p['gate']['codigo']:<6} | Distância Bruta: {p['dist']*100:.2f}% do mapa")

    # --- PASSO 3: SUAVIZAÇÃO E MAP MATCHING (BLENDING DE VETORES) ---
    # Cria uma lista de correções nos instantes das portas
    correcoes = []
    for p in filtro_passagens:
        t_event = p['t']
        pt_aligned = next(pt for pt in raw_aligned if pt['t'] == t_event)
        
        # Vetor de correção = (Coordenada real da porta) - (Coordenada alinhada odometria)
        cx = p['gate']['x_norm'] - pt_aligned['x']
        cy = p['gate']['y_norm'] - pt_aligned['y']
        correcoes.append((t_event, cx, cy))

    # Corrige toda a trajetória interpolando os vetores de correção ao longo do tempo
    corrigida = []
    for pt in raw_aligned:
        t = pt['t']
        
        if len(correcoes) == 0:
            cx, cy = 0.0, 0.0
        elif t <= correcoes[0][0]:
            # Antes da primeira porta: aplica a primeira correção de forma constante
            cx, cy = correcoes[0][1], correcoes[0][2]
        elif t >= correcoes[-1][0]:
            # Após a última porta: aplica a última correção de forma constante
            cx, cy = correcoes[-1][1], correcoes[-1][2]
        else:
            # Entre duas portas: interpola linearmente as correções
            for i in range(len(correcoes) - 1):
                t0, cx0, cy0 = correcoes[i]
                t1, cx1, cy1 = correcoes[i+1]
                if t0 <= t <= t1:
                    w = (t - t0) / (t1 - t0)
                    cx = (1 - w) * cx0 + w * cx1
                    cy = (1 - w) * cy0 + w * cy1
                    break
                    
        corrigida.append({
            "t": pt['t'],
            "x": round(pt['x'] + cx, 5),
            "y": round(pt['y'] + cy, 5),
            "evento": "caminho"
        })

    # Adiciona marcas visuais de evento nas portas para o Front-end saber
    for p in filtro_passagens:
        t_event = p['t']
        for pt in corrigida:
            if abs(pt['t'] - t_event) < 0.1:
                pt['evento'] = "passagem"
                pt['passagem_id'] = p['gate']['id']
                pt['codigo'] = p['gate']['codigo']

    # Salva o arquivo final de trajetória corrigida
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(corrigida, f, indent=2)
        
    print(f"\nTrajetória corrigida por Map Matching salva em: {output_path}")

if __name__ == '__main__':
    main()
