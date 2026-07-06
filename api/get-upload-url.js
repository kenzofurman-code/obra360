// api/get-upload-url.js

export default async function handler(req, res) {
  // Habilita CORS para desenvolvimento local se necessário
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

  const accountId = process.env.VITE_CLOUDFLARE_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  const token = process.env.VITE_CLOUDFLARE_STREAM_TOKEN || process.env.CLOUDFLARE_STREAM_TOKEN;

  if (!accountId || !token) {
    res.status(500).json({ error: 'Cloudflare credentials (ACCOUNT_ID/STREAM_TOKEN) are not configured on the server' });
    return;
  }

  try {
    const response = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/stream/direct_upload`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          maxDurationSeconds: 3600, // Limite de 1 hora de duração de vídeo
          expiry: new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString() // Expira em 4 horas
        })
      }
    );

    if (!response.ok) {
      const errText = await response.text();
      res.status(response.status).json({ error: `Cloudflare API error: ${errText}` });
      return;
    }

    const data = await response.json();
    
    // Retorna a URL do upload e o UID gerado
    res.status(200).json({
      uploadURL: data.result.uploadURL,
      uid: data.result.uid,
      accountId: accountId
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
