// Lit un flux NDJSON (une ligne JSON par événement) et appelle onEvent pour chacun.
// Utilisé par les variantes en flux du scan, de la synchronisation et de la recherche.
export async function readNdjson(
  url: string,
  init: RequestInit,
  onEvent: (event: any) => void,
): Promise<void> {
  const response = await fetch(url, init);
  if (!response.ok || !response.body) {
    throw new Error(`Échec de la requête (HTTP ${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const handleLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const event = JSON.parse(trimmed);
    if (event.type === 'error') throw new Error(event.detail || 'Erreur serveur');
    onEvent(event);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      handleLine(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  handleLine(buffer); // dernière ligne éventuelle sans saut final
}
