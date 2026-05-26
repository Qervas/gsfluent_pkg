/** Download a .gsq with progress + bounded retry. No byte-range resume:
 *  the backend serves Cache-Control: immutable, so the browser caches the
 *  whole file and a refetch after a transient failure is cheap. */
export interface DownloadProgress {
  received: number;
  total: number | null;
}

export async function downloadGsq(
  url: string,
  onProgress?: (p: DownloadProgress) => void,
  opts?: { retries?: number },
): Promise<ArrayBuffer> {
  const retries = opts?.retries ?? 2;
  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fetchWithProgress(url, onProgress);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

async function fetchWithProgress(
  url: string,
  onProgress?: (p: DownloadProgress) => void,
): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  const lenHeader = res.headers.get("content-length");
  const total = lenHeader ? Number(lenHeader) : null;

  if (!res.body) {
    const buf = await res.arrayBuffer();
    onProgress?.({ received: buf.byteLength, total });
    return buf;
  }

  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress?.({ received, total });
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out.buffer;
}
