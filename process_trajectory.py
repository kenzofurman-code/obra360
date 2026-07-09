# process_trajectory.py
import os
import sys
import json
import argparse
import math
import numpy as np

try:
    import cv2
except ImportError:
    print("Erro: A biblioteca 'opencv-python' nao esta instalada.")
    print("Por favor, instale executando: pip install opencv-python numpy")
    sys.exit(1)

def extract_trajectory(video_path, sample_rate=0.5):
    """
    Processa o video e extrai a trajetoria 2D usando um algoritmo robusto de
    Odometria Cinemática Equiretangular (E-VO), ideal para videos 360 estabilizados.
    Bypass total da ambiguidade rotacao-translacao dos crops de perspectiva convencionais.
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
    
    print(f"Video 360 Carregado: {width}x{height} @ {fps:.2f} FPS ({total_frames} frames)")

    # Parametros para rastreamento Lucas-Kanade
    lk_params = dict(
        winSize=(31, 31),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    )

    # Detector de cantos FAST
    detector = cv2.FastFeatureDetector_create(threshold=20, nonmaxSuppression=True)

    # Estado global da trajetoria
    pos_x = 0.0
    pos_y = 0.0
    yaw = 0.0  # Direcao absoluta acumulada (radianos)

    ret, frame = cap.read()
    if not ret:
        print("Erro ao ler o primeiro frame do video.")
        cap.release()
        return None

    # Recorta a banda horizontal media (30% a 70% da altura) para focar nas paredes
    # e evitar distorcoes extremas do teto e chao
    y1, y2 = int(height * 0.3), int(height * 0.7)
    band = frame[y1:y2, :]
    gray_prev = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)

    # Detecta pontos iniciais
    pts_prev = detector.detect(gray_prev)
    pts_prev = np.array([p.pt for p in pts_prev], dtype=np.float32).reshape(-1, 1, 2)

    waypoints = []
    # Salva o ponto inicial (tempo = 0s)
    waypoints.append({
        "t": 0.0,
        "x": pos_x,
        "y": pos_y
    })

    frame_idx = 1
    last_sampled_time = 0.0

    print("Processando odometria equiretangular...")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            band = frame[y1:y2, :]
            gray_curr = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
            current_time = frame_idx / fps

            # Re-detecta pontos se o número de pontos rastreados cair demais
            if pts_prev is None or len(pts_prev) < 60:
                pts_prev = detector.detect(gray_prev)
                if pts_prev:
                    pts_prev = np.array([p.pt for p in pts_prev], dtype=np.float32).reshape(-1, 1, 2)
                else:
                    pts_prev = np.array([], dtype=np.float32).reshape(0, 1, 2)

            if len(pts_prev) >= 10:
                pts_curr, status, err = cv2.calcOpticalFlowPyrLK(gray_prev, gray_curr, pts_prev, None, **lk_params)

                if pts_curr is not None and status is not None:
                    good_prev = pts_prev[status == 1]
                    good_curr = pts_curr[status == 1]

                    if len(good_curr) >= 10:
                        # Calcula os deslocamentos horizontais e verticais de cada ponto
                        dxs = good_curr[:, 0] - good_prev[:, 0]
                        dys = good_curr[:, 1] - good_prev[:, 1]

                        # YAW (ROTAÇÃO): O deslocamento horizontal mediano representa o giro da camera.
                        # Numa imagem 360, girar a camera desloca todo o panorama horizontalmente.
                        median_dx = np.median(dxs)
                        
                        # Giro angular: 360 graus = largura completa da imagem (width)
                        d_yaw = -2.0 * math.pi * (median_dx / width)
                        
                        # Ignora giros absurdos causados por ruidos extremos
                        if abs(d_yaw) < 0.3:
                            yaw += d_yaw

                        # COMPENSAÇÃO DE ROTAÇÃO: Remove o deslocamento do giro para isolar a translação
                        dxs_compensated = dxs - median_dx
                        
                        # PASSO (SPEED): Magnitude media do fluxo compensado
                        flow_mag = np.sqrt(dxs_compensated**2 + dys**2)
                        mean_flow = np.mean(flow_mag)

                        # Limiar minimo de movimento para evitar drift parado
                        step = 0.0
                        if mean_flow > 0.6:
                            # Constante empirica de velocidade media humana
                            step = 0.0016 * mean_flow

                        # INTEGRAÇÃO KINEMÁTICA: O usuario sempre caminha na direcao do vetor de visao
                        pos_x += step * math.sin(yaw)
                        pos_y += step * math.cos(yaw)

                        # Prepara o proximo frame
                        gray_prev = gray_curr.copy()
                        pts_prev = good_curr.reshape(-1, 1, 2)
                    else:
                        pts_prev = None
                        gray_prev = gray_curr.copy()
                else:
                    pts_prev = None
                    gray_prev = gray_curr.copy()
            else:
                pts_prev = None
                gray_prev = gray_curr.copy()

            # Guarda os pontos da trajetoria no intervalo do sample_rate (ex: a cada 0.5s)
            if current_time - last_sampled_time >= sample_rate:
                waypoints.append({
                    "t": round(current_time, 1),
                    "x": float(pos_x),
                    "y": float(pos_y)
                })
                last_sampled_time = current_time
                if frame_idx % 200 == 0:
                    print(f"Progresso: {frame_idx}/{total_frames} frames ({current_time:.1f}s) | Yaw: {math.degrees(yaw):.1f}°")

            frame_idx += 1
            
    except Exception as e:
        print(f"\n[AVISO] O processamento de frames foi interrompido por um erro: {e}")
        print("Salvando a trajetoria calculada ate o momento...")
        
    finally:
        cap.release()
        
    print("Processamento concluido!")

    # NORMALIZACAO DA ESCALA DO CAMINHO (Encaixa a trajetoria num raio maximo de 2 unidades)
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
