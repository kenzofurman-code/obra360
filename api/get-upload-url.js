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
    // Inicia uma sessão de upload TUS diretamente no endpoint /stream do Cloudflare
    const response = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/stream`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Tus-Resumable': '1.0.0',
          'Upload-Length': String(fileSize),
          // maxDurationSeconds: 3600 (1 hora) codificado em base64 (MzYwMA==)
          'Upload-Metadata': 'maxDurationSeconds MzYwMA==',
        }
      }
    );

    if (!response.ok) {
      const errText = await response.text();
      res.status(response.status).json({ error: `Erro na API do Cloudflare: ${errText}` });
      return;
    }

    // O Cloudflare retorna a URL do recurso TUS criado no cabeçalho 'Location'
    const uploadURL = response.headers.get('Location');
    if (!uploadURL) {
      res.status(500).json({ error: 'Cabeçalho Location não retornado pelo Cloudflare.' });
      return;
    }

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
