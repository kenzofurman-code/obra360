# -*- coding: utf-8 -*-
# video_io.py
# Leitor de video via ffmpeg (subprocess + pipe), no lugar de cv2.VideoCapture.
#
# Motivo (2026-07-14): o ffmpeg embutido no build do opencv-python para Windows
# frequentemente falha em abrir certos codecs (confirmado com ProRes - o Pedro
# gravou um teste em ProRes pra comparar precisao de SLAM, e cv2.VideoCapture
# retornava isOpened()==False mesmo com o ffmpeg "de verdade" instalado a parte
# no sistema - o mesmo binario ja usado por worker.py::cortar_video_inicio e
# rodar_slam.py::reduzir_video). Isso NAO e' especifico de ProRes: e' generico
# de qualquer codec que o build do opencv nao decodifique bem - por isso a
# solucao aqui usa o ffmpeg do sistema diretamente pra decodificar em frames
# BGR crus, o que funciona pra qualquer codec que esse ffmpeg souber ler
# (H.264, H.265, ProRes, etc.) sem nenhum tratamento especial por formato -
# se um dia o Pedro voltar a gravar em H.264, o mesmo caminho de codigo
# continua servindo, sem precisar reverter nada.
#
# FFmpegVideoReader expoe a MESMA interface que o pipeline ja usa de
# cv2.VideoCapture (.isOpened(), .get(prop), .read(), .release()) - trocar a
# leitura em process_trajectory.py/gerar_quadros.py/rodar_slam.py foi so'
# substituir a chamada de abertura, sem reescrever a logica de cada script.
#
# Leitura e' SEQUENCIAL apenas (sem seek aleatorio) - suficiente pro pipeline
# hoje, que so faz uma varredura unica de cada video (nenhum dos 3 scripts
# usa cv2.CAP_PROP_POS_FRAMES).

import json
import subprocess
import tempfile
import time

import cv2
import numpy as np


def probe_video(path):
    """Retorna (fps, width, height, total_frames_estimado, duracao_s) via
    ffprobe. total_frames as vezes e' so' uma ESTIMATIVA (duracao*fps) quando
    o container nao guarda a contagem exata (nb_frames ausente) - o mesmo tipo
    de imprecisao que cv2.CAP_PROP_FRAME_COUNT ja tinha antes, entao nao muda
    nenhuma suposicao que o resto do pipeline ja fazia."""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,r_frame_rate,nb_frames,duration',
        '-show_entries', 'format=duration',
        '-of', 'json', path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    streams = info.get('streams') or []
    if not streams:
        raise RuntimeError(f"ffprobe nao encontrou nenhum stream de video em {path}")
    stream = streams[0]
    width = int(stream['width'])
    height = int(stream['height'])
    num, _, den = stream['r_frame_rate'].partition('/')
    fps = float(num) / float(den) if den and float(den) != 0 else float(num)

    duracao = None
    if stream.get('duration'):
        duracao = float(stream['duration'])
    elif info.get('format', {}).get('duration'):
        duracao = float(info['format']['duration'])

    total_frames = None
    if stream.get('nb_frames'):
        try:
            total_frames = int(stream['nb_frames'])
        except ValueError:
            total_frames = None
    if not total_frames and duracao:
        total_frames = int(round(duracao * fps))

    return fps, width, height, (total_frames or 0), (duracao or 0.0)


class FFmpegVideoReader:
    """Substituto de cv2.VideoCapture que decodifica via ffmpeg (subprocess +
    pipe de rawvideo BGR24) em vez do backend interno do opencv-python -
    funciona com qualquer codec que o ffmpeg do sistema souber ler."""

    def __init__(self, path):
        self.path = path
        self._opened = False
        self._proc = None
        self._primeiro_read = True
        self._stderr_tmp = None
        try:
            self.fps, self.width, self.height, self.total_frames, self.duration = probe_video(path)
        except FileNotFoundError:
            print("[video_io] ffprobe nao encontrado no PATH - instale o ffmpeg "
                  "(inclui ffprobe) e adicione ao PATH do sistema: https://ffmpeg.org/download.html")
            return
        except Exception as e:
            print(f"[video_io] Erro ao inspecionar video com ffprobe: {e}")
            return

        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            print(f"[video_io] ffprobe nao retornou dimensoes/fps validos para {path}")
            return

        self._frame_bytes = self.width * self.height * 3
        # A flag de "passthrough" (nao duplicar/descartar frame nenhum, so'
        # decodificar 1-pra-1) mudou de nome entre versoes do ffmpeg: '-vsync 0'
        # (legado) foi REMOVIDO em builds mais novos (confirmado em producao
        # 2026-07-14: 'Unrecognized option vsync' no ffmpeg do Pedro), que
        # exigem '-fps_mode passthrough' no lugar - mas esse nome novo NAO
        # existe em builds mais antigos (confirmado neste ambiente de teste,
        # ffmpeg 4.4.2: 'Unrecognized option fps_mode'). Sem saber de antemao
        # qual versao esta instalada em cada maquina, tenta as duas variantes
        # (e por ultimo sem nenhuma das duas, se for uma versao ainda mais
        # antiga/nova que nao reconheca nenhuma) - erros de opcao invalida
        # sao IMEDIATOS (o processo morre em milissegundos, antes de decodificar
        # qualquer coisa), entao da pra detectar e cair pro proximo candidato
        # rapido, sem atrasar a abertura do video de verdade.
        candidatos_flags = [['-fps_mode', 'passthrough'], ['-vsync', '0'], []]
        # stderr vai pra um arquivo temporario (nao PIPE, pra nao arriscar
        # deadlock com 2 pipes - stdout com os frames e stderr - enchendo ao
        # mesmo tempo sem ninguem consumindo o segundo) e nao mais DEVNULL:
        # se o ffmpeg falhar silenciosamente (confirmado em producao 2026-07-14
        # - 0 panoramas gerados, sem nenhum erro visivel), sem isso nao da pra
        # saber o motivo. bufsize tambem deixou de forcar um buffer gigante
        # (frame_bytes*4, ~190MB pra video 5760x2880) - usa o padrao do Python
        # (-1), que ja e' suficiente porque read() abaixo consome em pedacos;
        # um bufsize absurdamente grande e' um suspeito razoavel pra falha
        # silenciosa de pipe no Windows.
        for flags in candidatos_flags:
            cmd = ['ffmpeg', '-v', 'error', '-i', path,
                   '-f', 'rawvideo', '-pix_fmt', 'bgr24', *flags, '-']
            stderr_tmp = tempfile.TemporaryFile(mode='w+b')
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_tmp)
            except FileNotFoundError:
                print("[video_io] ffmpeg nao encontrado no PATH - instale o ffmpeg "
                      "e adicione ao PATH do sistema: https://ffmpeg.org/download.html")
                return
            time.sleep(0.3)  # tempo de sobra pro ffmpeg rejeitar uma opcao invalida
            if proc.poll() is not None and proc.returncode != 0:
                stderr_tmp.seek(0)
                erro = stderr_tmp.read().decode('utf-8', errors='replace').strip()
                stderr_tmp.close()
                if flags != candidatos_flags[-1]:
                    print(f"[video_io] ffmpeg rejeitou {flags} ({erro or 'sem stderr'}) "
                          "- tentando a proxima variante...")
                    continue
                # ultima tentativa tambem falhou - guarda o processo/stderr mesmo
                # assim, pra read() reportar o erro real na 1a leitura
                self._proc, self._stderr_tmp = proc, stderr_tmp
                self._opened = True
                return
            self._proc, self._stderr_tmp = proc, stderr_tmp
            self._opened = True
            return

    def _dump_stderr_ffmpeg(self):
        """Mostra o stderr do ffmpeg (se algo foi escrito) - chamado quando a
        primeira leitura de frame falha, pra nao ficar cego igual antes."""
        if self._stderr_tmp is None:
            return
        try:
            self._stderr_tmp.seek(0)
            conteudo = self._stderr_tmp.read().decode('utf-8', errors='replace').strip()
        except Exception:
            conteudo = ''
        if conteudo:
            print(f"[video_io] ffmpeg stderr:\n{conteudo}")
        else:
            print("[video_io] ffmpeg nao escreveu nada no stderr (processo pode ter "
                  "sido encerrado/travado sem erro explicito).")

    def isOpened(self):
        return self._opened

    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_FPS:
            return self.fps
        if prop_id == cv2.CAP_PROP_FRAME_COUNT:
            return self.total_frames
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return self.width
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return self.height
        return 0

    def read(self):
        """Mesma assinatura de cv2.VideoCapture.read(): (ret, frame_bgr)."""
        if not self._opened:
            return False, None
        buf = bytearray()
        faltam = self._frame_bytes
        while faltam > 0:
            pedaco = self._proc.stdout.read(faltam)
            if not pedaco:
                break
            buf.extend(pedaco)
            faltam -= len(pedaco)
        if len(buf) < self._frame_bytes:
            if self._primeiro_read:
                # falhou logo no 1o frame - antes isso passava em silencio
                # (stderr ia pro DEVNULL) e o pipeline inteiro terminava
                # "com sucesso" gerando 0 panoramas sem nenhum aviso. Mostra
                # o stderr do ffmpeg (se tiver) pra dar pista do motivo real.
                print(f"[video_io] Falha ao ler o 1o frame de {self.path} "
                      f"(recebido {len(buf)}/{self._frame_bytes} bytes).")
                self._dump_stderr_ffmpeg()
            return False, None
        self._primeiro_read = False
        frame = np.frombuffer(bytes(buf), dtype=np.uint8).reshape((self.height, self.width, 3))
        return True, frame

    def release(self):
        if not self._opened or self._proc is None:
            return
        try:
            self._proc.stdout.close()
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        if self._stderr_tmp is not None:
            try:
                self._stderr_tmp.close()
            except Exception:
                pass
        self._opened = False


def extrair_frame_no_tempo(path, t_seg, timeout=30):
    """
    Extrai UM frame exato no instante t_seg (segundos) via ffmpeg, SEM
    decodificar o video inteiro - usa '-ss' ANTES de '-i' ("input seeking" na
    documentacao do ffmpeg: seek rapido pelo keyframe do container mais
    proximo, decodificando so' os poucos frames entre ele e o alvo).

    Adicionado 2026-07-15 pra super_resolucao.py: a fusao multi-frame precisa
    buscar recortes em ate ~12 instantes espalhados pelo video inteiro por
    clique do usuario - abrir FFmpegVideoReader (SEQUENCIAL apenas, ver
    docstring da classe acima) pra isso decodificaria o video inteiro so' pra
    pegar uma dezena de frames, inviabilizando "rodar sob demanda" (o decode
    full-res ja e' o maior gargalo do pipeline segundo os [TIMING] de
    gerar_quadros.py/worker.py). Superset estrito: FFmpegVideoReader e' os
    3 scripts que ja a usam continuam identicos, essa e' uma funcao nova e
    independente.

    Retorna (True, frame_bgr) ou (False, None) se a extracao falhar.
    """
    try:
        fps, width, height, _, duracao = probe_video(path)
    except Exception as e:
        print(f"[video_io] Erro ao inspecionar video com ffprobe: {e}")
        return False, None
    if width <= 0 or height <= 0:
        return False, None
    if duracao:
        t_seg = max(0.0, min(t_seg, max(duracao - 1.0 / max(fps, 1.0), 0.0)))
    else:
        t_seg = max(0.0, t_seg)
    frame_bytes = width * height * 3
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{t_seg:.3f}', '-i', path,
           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        print("[video_io] ffmpeg nao encontrado no PATH.")
        return False, None
    except subprocess.TimeoutExpired:
        print(f"[video_io] Timeout ({timeout}s) extraindo frame em t={t_seg:.3f}s de {path}.")
        return False, None
    buf = proc.stdout
    if len(buf) < frame_bytes:
        erro = proc.stderr.decode('utf-8', errors='replace').strip()
        print(f"[video_io] Falha ao extrair frame em t={t_seg:.3f}s "
              f"(recebido {len(buf)}/{frame_bytes} bytes). {erro[:300]}")
        return False, None
    frame = np.frombuffer(buf[:frame_bytes], dtype=np.uint8).reshape((height, width, 3))
    return True, frame
