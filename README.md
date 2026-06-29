# Tour 360° — Vistorias de Obra

Player de vídeo 360° com sincronização de planta baixa para vistorias de obra. Grava waypoints no mapa e navega pelo vídeo clicando na planta.

## Stack

- **Frontend**: React + Vite + Tailwind CSS
- **Player 360°**: Video.js + videojs-vr (projeção equiretangular)
- **HLS**: hls.js (segmentos via Cloudflare Stream)
- **Banco de dados**: Firebase Firestore
- **Deploy**: Vercel

---

## Pré-requisitos

- Conta no [Firebase](https://console.firebase.google.com) (Firestore)
- Conta no [Cloudflare Stream](https://dash.cloudflare.com) (vídeos HLS)
- Repositório no GitHub
- Conta no [Vercel](https://vercel.com)

---

## 1. Configurar Firebase

1. Crie um projeto em [console.firebase.google.com](https://console.firebase.google.com)
2. Ative o **Firestore Database** (modo produção)
3. Vá em **Configurações do projeto → Seus apps → Web** e copie as credenciais
4. No Firestore, publique as regras do arquivo `firebase/firestore.rules`

---

## 2. Configurar Cloudflare R2

O R2 substitui o Cloudflare Stream com custo zero até 10 GB. Você converte o vídeo localmente e sobe os arquivos HLS prontos.

### 2a. Instalar FFmpeg (Windows)
Baixe em [ffmpeg.org/download](https://ffmpeg.org/download.html) e adicione ao PATH.

### 2b. Converter o vídeo para HLS

Exporte o vídeo do Insta360 Studio como MP4 equiretangular e rode:

```bash
ffmpeg -i video_obra.mp4 \
  -codec: copy \
  -hls_time 4 \
  -hls_list_size 0 \
  -f hls \
  pasta_saida/index.m3u8
```

Isso gera `index.m3u8` + vários arquivos `segNNN.ts` na pasta de saída.

### 2c. Criar bucket no R2

1. Acesse [dash.cloudflare.com](https://dash.cloudflare.com) → **R2 Object Storage**
2. Crie um bucket (ex: `tour360-obra`)
3. Vá em **Settings → Public Access → Allow Access** — copie o domínio público (ex: `pub-abc123.r2.dev`)
4. Suba toda a pasta de saída mantendo a estrutura:
   ```
   tour360-obra/
   └── 5pav_2024-06-01/
       ├── index.m3u8
       ├── seg000.ts
       ├── seg001.ts
       └── ...
   ```
5. A URL do vídeo será: `https://pub-abc123.r2.dev/5pav_2024-06-01/index.m3u8`

### 2d. Subir as plantas PNG

No mesmo bucket, crie uma pasta `plantas/` e suba os PNGs:
- URL resultante: `https://pub-abc123.r2.dev/plantas/5pav.png`

### 2e. Configurar CORS no R2

No bucket → **Settings → CORS Policy**, adicione:

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

> Sem isso o player bloqueia a requisição HLS no browser.

---

## 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
```

Preencha `.env` com as credenciais do Firebase:

```env
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=...
VITE_FIREBASE_PROJECT_ID=...
VITE_FIREBASE_STORAGE_BUCKET=...
VITE_FIREBASE_MESSAGING_SENDER_ID=...
VITE_FIREBASE_APP_ID=...
```

---

## 4. Rodar localmente

```bash
npm install
npm run dev
```

---

## 5. Deploy no Vercel

1. Suba o projeto para o GitHub
2. No Vercel: **Import Project** → selecione o repositório
3. Em **Environment Variables**, adicione as mesmas variáveis do `.env`
4. Deploy automático a cada `git push`

---

## Fluxo de uso

### Cadastrar nova visita
1. Clique em **+ Nova visita**
2. Selecione o pavimento
3. Cole o **Video ID** do Cloudflare Stream
4. (Opcional) Cole a URL pública da planta PNG do pavimento
5. Salve — o app abre direto no editor de waypoints

### Definir waypoints (uma vez por visita)
1. Reproduza o vídeo 360° e pause no ponto desejado
2. Clique em **+ Adicionar waypoint**
3. Clique na posição correspondente na planta
4. Digite o nome do ponto (ex: "Apto 501 — Sala") e confirme
5. Repita para todos os pontos do percurso
6. Clique em **Salvar waypoints**

### Navegar pelo tour
- **Clique na planta** → vídeo pula para o trecho correspondente
- **Vídeo avança** → ponto verde se move na planta automaticamente
- **Clique num pin amarelo** → pula para aquele waypoint

---

## Estrutura do projeto

```
src/
├── components/
│   ├── Player360.jsx      # Video.js + VR plugin
│   ├── PlantaViewer.jsx   # Canvas com trajetória animada
│   └── WaypointEditor.jsx # Painel de gestão de waypoints
├── hooks/
│   └── useVideoSync.js    # Interpolação posição ↔ tempo
├── lib/
│   ├── firebase.js        # Inicialização Firebase
│   └── visitas.js         # CRUD Firestore
└── pages/
    ├── Home.jsx            # Lista de visitas
    ├── Upload.jsx          # Cadastro de nova visita
    └── Visita.jsx          # Player + planta + waypoints
```

---

## Planta PNG — onde hospedar

A planta precisa de uma URL pública acessível via HTTPS. Opções simples:

- **Firebase Storage** (mesmo projeto) — recomendado
- **Supabase Storage** (se já usa no tracker de obra)
- **Cloudflare R2** — grátis até 10 GB
- Qualquer CDN público com CORS habilitado

---

## Schema Firebase (Firestore)

```
visitas/{visitaId}
  ├── pavimento: "5° Pavimento"
  ├── data: Timestamp
  ├── hls_url: "https://pub-xxx.r2.dev/5pav_2024-06-01/index.m3u8"
  ├── thumbnail_url: "https://pub-xxx.r2.dev/5pav_2024-06-01/thumb.jpg"
  ├── planta_url: "https://pub-xxx.r2.dev/plantas/5pav.png"
  ├── duracao_segundos: 420
  ├── status: "ready"
  └── waypoints: [
        { t: 0,   x: 0.12, y: 0.45, label: "Entrada", observacao: "" },
        { t: 38,  x: 0.34, y: 0.45, label: "Corredor principal" },
        ...
      ]
```
