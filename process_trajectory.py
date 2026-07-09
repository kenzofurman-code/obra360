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

class ExtendedKalmanFilter:
    def __init__(self):
        # Estado inicial: [px, py, v, theta]
        # px, py: Posicao no plano local
        # v: Velocidade de caminhada (m/s)
        # theta: Orientacao yaw absoluta (radianos)
        self.x = np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        
        # Matriz de covariancia de erro inicial P
        self.P = np.diag([0.01, 0.01, 0.1, 0.1]).astype(np.float32)
        
        # Ruido do modelo fisico (Process Noise Q)
        # Modelamos o desvio maximo de aceleracao linear e giro angular do operador
        self.Q = np.diag([1e-6, 1e-6, 0.01, 0.02]).astype(np.float32)
        
        # Matriz de observacao H (linear: lemos v e theta diretamente do processador visual)
        self.H = np.array([
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ], dtype=np.float32)

    def predict(self, dt):
        px, py, v, theta = self.x[0], self.x[1], self.x[2], self.x[3]
        
        # Predicao cinemática nao-linear
        self.x[0] = px + v * math.sin(theta) * dt
        self.x[1] = py + v * math.cos(theta) * dt
        # v e theta sao mantidos constantes na predição física (modelo de velocidade)
        
        # Jacobiana F da equacao cinemática
        F = np.array([
            [1.0, 0.0, math.sin(theta) * dt, v * math.cos(theta) * dt],
            [0.0, 1.0, math.cos(theta) * dt, -v * math.sin(theta) * dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float32)
        
        # P = F * P * F.T + Q
        self.P = np.dot(np.dot(F, self.P), F.T) + self.Q

    def update(self, z_v, z_theta, is_static=False):
        # Ruido de medicao R
        # ZUPT (Zero Velocity Update): Se estiver parado, reduzimos drasticamente
        # o ruido de velocidade para forcar o filtro a travar a posicao.
        sigma_v = 0.005 if is_static else 0.15
        sigma_theta = 0.02  # Pixels na horizontal do panorama dao alta precisao angular
        R = np.diag([sigma_v**2, sigma_theta**2]).astype(np.float32)
        
        z = np.array([z_v, z_theta], dtype=np.float32)
        
        # Inovacao y = z - H * x
        y = z - np.dot(self.H, self.x)
        
        # S = H * P * H.T + R
        S = np.dot(np.dot(self.H, self.P), self.H.T) + R
        
        # Ganho de Kalman K = P * H.T * inv(S)
        K = np.dot(np.dot(self.P, self.H.T), np.linalg.inv(S))
        
        # Atualiza vetor de estados x = x + K * y
        self.x = self.x + np.dot(K, y)
        
        # Atualiza covariancia P = (I - K * H) * P
        I = np.eye(4, dtype=np.float32)
        self.P = np.dot(I - np.dot(K, self.H), self.P)

def extract_trajectory(video_path, sample_rate=0.5):
    """
    Processa o video e extrai a trajetoria 2D usando um algoritmo robusto de
    Odometria Cinemática Equiretangular combinada com Filtro de Kalman Estendido (EKF-VO).
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
    dt = 1.0 / fps
    
    print(f"Video 360 Carregado: {width}x{height} @ {fps:.2f} FPS ({total_frames} frames)")

    # Parametros para rastreamento Lucas-Kanade
    lk_params = dict(
        winSize=(31, 31),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    )

    # Detector de cantos FAST
    detector = cv2.FastFeatureDetector_create(threshold=20, nonmaxSuppression=True)

    ret, frame = cap.read()
    if not ret:
        print("Erro ao ler o primeiro frame do video.")
        cap.release()
        return None

    # Banda horizontal media (30% a 70% da altura) para focar nas paredes
    y1, y2 = int(height * 0.3), int(height * 0.7)
    band = frame[y1:y2, :]
    gray_prev = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)

    # Detecta pontos iniciais
    pts_prev = detector.detect(gray_prev)
    pts_prev = np.array([p.pt for p in pts_prev], dtype=np.float32).reshape(-1, 1, 2)

    # Inicializa EKF e variavel de yaw medido
    ekf = ExtendedKalmanFilter()
    measured_yaw = 0.0

    waypoints = []
    # Salva o ponto inicial (tempo = 0s)
    waypoints.append({
        "t": 0.0,
        "x": 0.0,
        "y": 0.0
    })

    frame_idx = 1
    last_sampled_time = 0.0

    print("Processando odometria cinemática EKF...")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            band = frame[y1:y2, :]
            gray_curr = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
            current_time = frame_idx / fps

            # Re-detecta pontos se o número cair demais
            if pts_prev is None or len(pts_prev) < 60:
                pts_prev = detector.detect(gray_prev)
                if pts_prev:
                    pts_prev = np.array([p.pt for p in pts_prev], dtype=np.float32).reshape(-1, 1, 2)
                else:
                    pts_prev = np.array([], dtype=np.float32).reshape(0, 1, 2)

            # Predicao cinemática do EKF
            ekf.predict(dt)

            is_static = True
            measured_speed = 0.0

            if len(pts_prev) >= 10:
                pts_curr, status, err = cv2.calcOpticalFlowPyrLK(gray_prev, gray_curr, pts_prev, None, **lk_params)

                if pts_curr is not None and status is not None:
                    good_prev = pts_prev[status == 1]
                    good_curr = pts_curr[status == 1]

                    if len(good_curr) >= 10:
                        dxs = good_curr[:, 0] - good_prev[:, 0]
                        dys = good_curr[:, 1] - good_prev[:, 1]

                        # YAW (ROTACAO): Deslocamento horizontal mediano
                        median_dx = np.median(dxs)
                        d_yaw = -2.0 * math.pi * (median_dx / width)
                        
                        # Ignora saltos de giro absurdos
                        if abs(d_yaw) < 0.3:
                            measured_yaw += d_yaw

                        # COMPENSAÇÃO DE ROTAÇÃO: Isola a translacao pura
                        dxs_compensated = dxs - median_dx
                        
                        # PASSO: Magnitude media do fluxo compensado
                        flow_mag = np.sqrt(dxs_compensated**2 + dys**2)
                        mean_flow = np.mean(flow_mag)

                        if mean_flow > 0.6:
                            # Velocidade medida = passo / dt
                            step = 0.0016 * mean_flow
                            measured_speed = step / dt
                            is_static = False

                        # Atualiza EKF com as observações
                        ekf.update(measured_speed, measured_yaw, is_static=is_static)

                        # Prepara o proximo frame
                        gray_prev = gray_curr.copy()
                        pts_prev = good_curr.reshape(-1, 1, 2)
                    else:
                        ekf.update(0.0, measured_yaw, is_static=True)
                        pts_prev = None
                        gray_prev = gray_curr.copy()
                else:
                    ekf.update(0.0, measured_yaw, is_static=True)
                    pts_prev = None
                    gray_prev = gray_curr.copy()
            else:
                ekf.update(0.0, measured_yaw, is_static=True)
                pts_prev = None
                gray_prev = gray_curr.copy()

            # Guarda os pontos filtrados no intervalo do sample_rate (ex: a cada 0.5s)
            if current_time - last_sampled_time >= sample_rate:
                waypoints.append({
                    "t": round(current_time, 1),
                    "x": float(ekf.x[0]), # Posicao X filtrada
                    "y": float(ekf.x[1])  # Posicao Y filtrada
                })
                last_sampled_time = current_time
                if frame_idx % 200 == 0:
                    print(f"Progresso: {frame_idx}/{total_frames} frames ({current_time:.1f}s) | Pos: ({ekf.x[0]:.2f}, {ekf.x[1]:.2f}) | Vel: {ekf.x[2]:.2f}m/s | Yaw: {math.degrees(ekf.x[3]):.1f}°")

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
    parser = argparse.ArgumentParser(description="Gera trajetoria 2D filtrada por EKF a partir de video 360 para o Obra360.")
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
