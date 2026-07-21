const GITHUB_API = 'https://api.github.com';
const DATA_PATH = '_data/reportes.json';

async function ghRequest(path, options = {}) {
  const res = await fetch(`${GITHUB_API}${path}`, {
    ...options,
    headers: {
      Authorization: `token ${process.env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.message || `Error de GitHub (${res.status})`);
  }
  return data;
}

exports.handler = async (event) => {
  if (!['POST', 'PUT', 'DELETE'].includes(event.httpMethod)) {
    return { statusCode: 405, body: JSON.stringify({ error: 'Método no permitido' }) };
  }

  let payload;
  try {
    payload = JSON.parse(event.body || '{}');
  } catch (e) {
    return { statusCode: 400, body: JSON.stringify({ error: 'JSON inválido' }) };
  }

  if (!payload.password || payload.password !== process.env.EDITOR_PASSWORD) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Clave incorrecta' }) };
  }

  const repo = process.env.GITHUB_REPO; // formato "owner/repo"
  const branch = process.env.GITHUB_BRANCH || 'main';

  try {
    const current = await ghRequest(`/repos/${repo}/contents/${DATA_PATH}?ref=${branch}`);
    const currentJson = JSON.parse(Buffer.from(current.content, 'base64').toString('utf-8'));
    const reportes = currentJson.reportes || [];

    const report = payload.report || {};

    if (payload.imageBase64 && payload.imageName) {
      const safeName = payload.imageName.replace(/[^a-zA-Z0-9.\-_]/g, '_');
      const imagePath = `imagenes/reportes/${Date.now()}-${safeName}`;
      const rawBase64 = payload.imageBase64.replace(/^data:.*;base64,/, '');
      await ghRequest(`/repos/${repo}/contents/${imagePath}`, {
        method: 'PUT',
        body: JSON.stringify({
          message: `Subir foto de reporte: ${safeName}`,
          content: rawBase64,
          branch,
        }),
      });
      report.image = `/${imagePath}`;
    }

    if (event.httpMethod === 'POST') {
      reportes.push(report);
    } else if (event.httpMethod === 'PUT') {
      const idx = payload.index;
      if (typeof idx !== 'number' || !reportes[idx]) {
        return { statusCode: 400, body: JSON.stringify({ error: 'Reporte no encontrado' }) };
      }
      reportes[idx] = { ...reportes[idx], ...report, image: report.image || reportes[idx].image };
    } else if (event.httpMethod === 'DELETE') {
      const idx = payload.index;
      if (typeof idx !== 'number' || !reportes[idx]) {
        return { statusCode: 400, body: JSON.stringify({ error: 'Reporte no encontrado' }) };
      }
      reportes.splice(idx, 1);
    }

    const newContent = Buffer.from(JSON.stringify({ reportes }, null, 2)).toString('base64');
    await ghRequest(`/repos/${repo}/contents/${DATA_PATH}`, {
      method: 'PUT',
      body: JSON.stringify({
        message: 'Actualizar reportes desde /editar.html',
        content: newContent,
        sha: current.sha,
        branch,
      }),
    });

    return { statusCode: 200, body: JSON.stringify({ ok: true }) };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message }) };
  }
};
