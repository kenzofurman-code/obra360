// api/get-upload-url.js

export default async function handler(req, res) {
  // Habilita CORS para desenvolvimento local
  res.setHeader('Access-Control-Allow-Credentials', true);
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS,PATCH,DELETE,POST,PUT');
  res.setHeader(
    'Access-Control-Allow-Headers',
    'X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version'
  );

  if (req.method === 'OPTIONS') {
    res.status(200).end();
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  const { fileSize } = req.body;
  if (!fileSize) {
    res.status(400).json({ error: 'O tamanho do arquivo (fileSize) é obrigatório.' });
    return;
  }

  const accountId = process.env.VITE_CLOUDFLARE_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  const token = process.env.VITE_CLOUDFLARE_STREAM_TOKEN || process.env.CLOUDFLARE_STREAM_TOKEN;

  if (!accountId || !token) {
    res.status(500).json({ error: 'Credenciais do Cloudflare não configuradas no servidor.' });
    return;
  }

  try {
    // CRITICAL: Adicionamos ?direct_user=true no final do endpoint para sinalizar que o upload
    // será feito diretamente pelo navegador (cliente) e obter a URL com CORS liberado.
    const response = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/stream?direct_user=true`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Tus-Resumable': '1.0.0',
          'Upload-Length': String(fileSize),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          maxDurationSeconds: 3600, // Limite de 1 hora
        })
      }
    );

    if (!response.ok) {
      const errText = await response.text();
      res.status(response.status).json({ error: `Erro na API do Cloudflare: ${errText}` });
      return;
    }

    // O Cloudflare retorna a URL do recurso TUS criado no cabeçalho 'Location'
    let uploadURL = response.headers.get('Location');
    if (!uploadURL) {
      res.status(500).json({ error: 'Cabeçalho Location não retornado pelo Cloudflare.' });
      return;
    }

    // Substituição preventiva: garante que a URL use o domínio de upload CORS-friendly do Cloudflare
    uploadURL = uploadURL
      .replace('edge-production.gateway.api.cloudflare.com', 'upload.videodelivery.net')
      .replace('api.cloudflare.com', 'upload.videodelivery.net');

    // O UID do vídeo é o último segmento do caminho da URL do recurso TUS
    const uid = uploadURL.split('/').pop().split('?')[0];

    res.status(200).json({
      uploadURL,
      uid,
      accountId
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
