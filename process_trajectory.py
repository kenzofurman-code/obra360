# process_trajectory.py
import os
import sys
import json
import argparse
import numpy as np

try:
    import cv2
except ImportError:
    print("Erro: A biblioteca 'opencv-python' nao esta instalada.")
    print("Por favor, instale executando: pip install opencv-python numpy")
    sys.exit(1)

def extract_trajectory(video_path, sample_rate=0.5):
    """
    Processa o video e extrai a trajetoria 2D usando um modelo robusto de
    Odometria Visual 2D (Yaw-only e velocidade adaptativa por fluxo optico).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Erro ao abrir o video: {video_path}")
        return None

    # Propriedades do video
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    print(f"Video carregado: {width}x{height} @ {fps:.2f} FPS ({total_frames} frames)")

    # Define a regiao da camera de perspectiva (crop central de 90 graus FOV)
    w_crop = width // 4
    h_crop = height // 2
    
    x_offset = (width - w_crop) // 2
    y_offset = (height - h_crop) // 2

    # Matriz intrinseca estimada da camera (K) baseada no crop
    focal_length = w_crop / 2.0
    cx = w_crop / 2.0
    cy = h_crop / 2.0
    K = np.array([
        [focal_length, 0, cx],
        [0, focal_length, cy],
        [0, 0, 1]
    ], dtype=np.float32)

    # Parametros para rastreamento Lucas-Kanade
    lk_params = dict(
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    )

    # Estado da trajetoria 2D
    pos_x = 0.0
    pos_y = 0.0
    yaw = 0.0  # Angulo acumulado de direcao (azimute relativo)

    ret, prev_frame = cap.read()
    if not ret:
        print("Erro ao ler o primeiro frame do video.")
        cap.release()
        return None

    # Corta a area central e converte para escala de cinza
    prev_crop = prev_frame[y_offset:y_offset+h_crop, x_offset:x_offset+w_crop]
    prev_gray = cv2.cvtColor(prev_crop, cv2.COLOR_BGR2GRAY)

    # Detecta pontos de interesse iniciais (FAST)
    detector = cv2.FastFeatureDetector_create(threshold=25, nonmaxSuppression=True)
    prev_pts = detector.detect(prev_gray)
    prev_pts = np.array([p.pt for p in prev_pts], dtype=np.float32).reshape(-1, 1, 2)

    waypoints = []
    frame_idx = 1
    
    # Salva o ponto inicial (tempo = 0s)
    waypoints.append({
        "t": 0.0,
        "x": pos_x,
        "y": pos_y
    })

    last_sampled_time = 0.0

    print("Processando trajetoria (este processo pode demorar alguns minutos)...")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Crop e escala de cinza
            crop = frame[y_offset:y_offset+h_crop, x_offset:x_offset+w_crop]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            
            current_time = frame_idx / fps

            # Se nao houver pontos suficientes para rastrear, detecta novos
            if len(prev_pts) < 150:
                new_pts = detector.detect(prev_gray)
                if new_pts:
                    new_pts_arr = np.array([p.pt for p in new_pts], dtype=np.float32).reshape(-1, 1, 2)
                    prev_pts = np.vstack((prev_pts, new_pts_arr))

            if len(prev_pts) > 0:
                # Rastreia os pontos usando Optical Flow
                curr_pts, status, err = cv2.calcOpticalFlowPyrLK(prev_gray, gray, prev_pts, None, **lk_params)

                if status is not None:
                    # Filtra os pontos rastreados com sucesso
                    good_prev = prev_pts[status == 1]
                    good_curr = curr_pts[status == 1]

                    if len(good_prev) > 10:
                        # 1. Calcula o deslocamento medio em pixels para detectar velocidade e pausas
                        displacements = np.sqrt(np.sum((good_curr - good_prev) ** 2, axis=1))
                        avg_displacement = np.mean(displacements)

                        # Filtro de ruido
                        if avg_displacement > 1.8:
                            # Estima a Matriz Essencial
                            E, inliers = cv2.findEssentialMat(
                                good_curr, good_prev, K, 
                                method=cv2.RANSAC, prob=0.999, threshold=1.0
                            )

                            if E is not None and E.shape == (3, 3):
                                # Recupera a Rotacao e Translacao da camera
                                _, R, t, mask = cv2.recoverPose(E, good_curr, good_prev, K)

                                if np.sum(mask) > 8:
                                    # Extrai o angulo relativo de rotacao (Yaw)
                                    theta = np.arctan2(R[0, 2], R[2, 2])
                                    
                                    # Limita a taxa de rotacao por frame
                                    theta = np.clip(theta, -0.15, 0.15)
                                    yaw += theta

                                    # Vetor de translacao
                                    tx = t[0, 0]
                                    tz = t[2, 0]

                                    t_len = np.sqrt(tx*tx + tz*tz)
                                    if t_len > 0:
                                        tx /= t_len
                                        tz /= t_len

                                    # A velocidade do passo e proporcional a taxa de mudanca de pixels
                                    step = 0.0035 * avg_displacement

                                    # Projeta e acumula no plano 2D usando a direcao (yaw) acumulada
                                    dx = tx * np.cos(yaw) - tz * np.sin(yaw)
                                    dy = tx * np.sin(yaw) + tz * np.cos(yaw)

                                    pos_x += step * dx
                                    pos_y += step * dy

                    # Prepara para o proximo frame
                    prev_pts = good_curr.reshape(-1, 1, 2)
            
            # Guarda os pontos da trajetoria no intervalo do sample_rate (ex: a cada 0.5s)
            if current_time - last_sampled_time >= sample_rate:
                waypoints.append({
                    "t": round(current_time, 1),
                    "x": float(pos_x),
                    "y": float(pos_y)
                })
                last_sampled_time = current_time
                print(f"Progresso: {frame_idx}/{total_frames} frames ({current_time:.1f}s processados)")

            prev_gray = gray.copy()
            frame_idx += 1
            
    except Exception as e:
        print(f"\n[AVISO] O processamento de frames foi interrompido por um erro: {e}")
        print("Salvando a trajetoria calculada ate o momento do erro...")
        
    finally:
        cap.release()
        
    print("Processamento concluido!")

    # NORMALIZACAO DA ESCALA DO CAMINHO
    max_dist = 0.0
    for wp in waypoints:
        dist = np.sqrt(wp["x"]**2 + wp["y"]**2)
        if dist > max_dist:
            max_dist = dist
            
    if max_dist > 0:
        scale_factor = 2.0 / max_dist
        for wp in waypoints:
            wp["x"] *= scale_factor
            wp["y"] *= scale_factor

    return waypoints

def main():
    parser = argparse.ArgumentParser(description="Gera trajetoria 2D a partir de video 360 para o Obra360.")
    parser.add_argument("--video", required=True, help="Caminho para o arquivo de video MP4.")
    parser.add_argument("--out", default="caminho_vistoria.json", help="Nome do arquivo JSON de saida.")
    parser.add_argument("--rate", type=float, default=0.5, help="Taxa de amostragem em segundos (padrao: 0.5).")

    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"Arquivo nao encontrado: {args.video}")
        sys.exit(1)

    waypoints = extract_trajectory(args.video, sample_rate=args.rate)
    
    if waypoints and len(waypoints) > 0:
        try:
            with open(args.out, 'w', encoding='utf-8') as f:
                json.dump(waypoints, f, indent=2)
            print(f"Trajetoria salva com sucesso em: {args.out}")
            print("Agora voce pode subir este arquivo JSON no site do Obra360 junto com o seu video!")
        except Exception as file_error:
            print(f"Erro ao salvar arquivo JSON: {file_error}")
    else:
        print("Falha ao gerar a trajetoria.")

if __name__ == "__main__":
    main()
