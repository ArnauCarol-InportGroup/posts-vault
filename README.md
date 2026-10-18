# Posts

El contenido de este repositorio está cifrado (AES-256-GCM). Sin la contraseña no se puede leer.

## Cómo funciona

`tools/buffer_fill.py` rellena los huecos libres de la cola de Buffer (como máximo 10 posts programados) con los siguientes posts del vault, en orden. Cuando llega al último, vuelve a empezar por el primero.

- `vault/*.bin`: cada post (vídeo, texto y portada) cifrado, con el nombre también ofuscado.
- `vault/state.bin`: el siguiente post y el historial, cifrados. No hace falta base de datos.
- Buffer solo acepta vídeos por URL pública. El workflow descifra cada vídeo que programa y lo sube con un nombre aleatorio a la release `media`. Lo borra cuando Buffer ya ha publicado el post.

## Uso

| Qué | Cómo |
|---|---|
| Rellenar Buffer ahora | Doble clic en `Rellenar Buffer.bat`, o Actions → *Rellenar Buffer* → *Run workflow* |
| Automático | Una vez por semana: lunes a las 06:00 UTC |
| Añadir o editar posts | Crea o edita la carpeta en local y haz doble clic en `Sincronizar posts.bat` |
| Recuperar las carpetas | `python tools/vault.py decrypt` |
| Ver el contenido y el siguiente post | `python tools/vault.py list` |

En local, la contraseña se lee de `POSTS_PASSWORD` en `.env`, o se pide al ejecutar.

## Secrets de GitHub

- `BUFFER_KEY`: la clave de la API de Buffer.
- `POSTS_PASSWORD`: la contraseña del vault.
