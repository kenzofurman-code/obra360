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
    Processa o video e extrai a trajetoria 2D (plano XZ) usando Odometria Visual Monocular.
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
    # 360 horizontal = width, entao 90 graus = width / 4
    # 180 vertical = height, entao 90 graus = height / 2
    w_crop = width // 4
    h_crop = height // 2
    
    x_offset = (width - w_crop) // 2
    y_offset = (height - h_crop) // 2

    # Matriz intrinseca intrinseca estimada da camera (K) baseada no crop
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

    # Estado da trajetoria
    pos = np.zeros((3, 1), dtype=np.float64)  # Posicao [X, Y, Z]
    rot = np.eye(3, dtype=np.float64)        # Rotacao [3x3]

    ret, prev_frame = cap.read()
    if not ret:
        print("Erro ao ler o primeiro frame do video.")
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
        "x": float(pos[0, 0]),
        "y": float(pos[2, 0]) # Usamos o eixo Z do SLAM como o Y da planta 2D
    })

    last_sampled_time = 0.0

    print("Processando trajetoria (este processo pode demorar alguns minutos)...")

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

        # Rastreia os pontos usando Optical Flow
        curr_pts, status, err = cv2.calcOpticalFlowPyrLK(prev_gray, gray, prev_pts, None, **lk_params)

        # Filtra os pontos rastreados com sucesso
        good_prev = prev_pts[status == 1]
        good_curr = curr_pts[status == 1]

        if len(good_prev) > 10:
            # Estima a Matriz Essencial
            E, inliers = cv2.findEssentialMat(
                good_curr, good_prev, K, 
                method=cv2.RANSAC, prob=0.999, threshold=1.0
            )

            if E is not None and E.shape == (3, 3):
                # Recupera a Rotacao e Translacao da camera
                _, R, t, mask = cv2.recoverPose(E, good_curr, good_prev, K)

                # Mantem escala unitaria constante ja que a escala real do SLAM monocular e ambigua
                # (A escala sera ajustada interativamente na planta pelo usuario)
                scale = 0.08  # Constante de passo aproximada por frame

                # Atualiza posicao e orientacao
                # Apenas atualiza se houver inliers suficientes na matriz de pose
                if np.sum(mask) > 5:
                    pos = pos + scale * rot.dot(t)
                    rot = R.dot(rot)

        # Guarda os pontos da trajetoria no intervalo do sample_rate (ex: a cada 0.5s)
        if current_time - last_sampled_time >= sample_rate:
            waypoints.append({
                "t": round(current_time, 1),
                "x": float(pos[0, 0]),
                "y": float(pos[2, 0]) # Projecao do plano Z (profundidade)
            })
            last_sampled_time = current_time
            print(f"Progresso: {frame_idx}/{total_frames} frames ({current_time:.1f}s processados)")

        # Prepara para o proximo frame
        prev_gray = gray.copy()
        prev_pts = good_curr.reshape(-1, 1, 2)
        frame_idx += 1

    cap.release()
    print("Processamento concluido!")
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
    
    if waypoints:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(waypoints, f, indent=2)
        print(f"Trajetoria salva com sucesso em: {args.out}")
        print("Agora voce pode subir este arquivo JSON no site do Obra360 junto com o seu video!")
    else:
        print("Falha ao gerar a trajetoria.")

if __name__ == "__main__":
    main()
