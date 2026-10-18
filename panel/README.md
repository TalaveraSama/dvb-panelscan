# Panel administrativo DVB/TBS

Interfaz web para administrar el escáner **dvb2m3u8**: ver adaptadores, lanzar
escaneos, listar canales con EPG now/next y exportar la lista **M3U8**.

Construido con **HTML/JS + la biblioteca estándar de Python** (cero
dependencias externas).

```
panel/
├── server.py          ← servidor HTTP + API JSON
├── static/
│   ├── index.html     ← interfaz
│   ├── app.js         ← lógica del frontend
│   └── style.css      ← estilos
└── README.md
```

## Arrancar

```sh
python server.py                # -> http://127.0.0.1:8080
python server.py --port 9000    # otro puerto
```

Abre **http://127.0.0.1:8080** en el navegador.

## Modo DEMO vs modo real

- **Windows / sin binario** → el panel arranca en **modo demo** con canales de
  ejemplo, para que puedas ver la interfaz ya.
- **Linux con `dvb2m3u8` compilado** → el panel detecta el binario
  (en `../dvb2m3u8/dvb2m3u8` o en el `PATH`) y lanza escaneos **reales** contra
  el hardware DVB/TBS.

Para forzar la ruta del binario:

```sh
python server.py --dvb2m3u8 /ruta/al/dvb2m3u8
```

> El binario se compila en `../dvb2m3u8/` con `make`. Necesita la extensión
> `--json` que se añadió en este proyecto.

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/status` | Plataforma, modo demo, adaptadores detectados |
| `POST` | `/api/scan` | Lanza un escaneo; cuerpo JSON con parámetros |
| `GET` | `/api/channels` | Canales del último escaneo (o demo) |
| `GET` | `/api/playlist.m3u8` | Exporta la lista M3U8 |

### POST /api/scan

```json
{
  "type": "dvbt",
  "freq_mhz": 674,
  "symbol_rate": null,
  "pol": "H",
  "adapter": 0,
  "timeout": 15
}
```

`type` puede ser `dvbt`, `dvbt2`, `dvbc`, `dvbs` o `dvbs2`. `symbol_rate` es
obligatorio para `dvbc`/`dvbs`/`dvbs2`. Alternativamente `freq` (en Hz).

Respuesta (real o demo):

```json
{
  "ok": true,
  "demo": false,
  "adapter": 0,
  "frequency": 674000000,
  "network": "RTVE",
  "signal": 43210,
  "services": [
    {"sid": 101, "name": "Canal 24h", "provider": "RTVE",
     "type": 1, "now": "Noticias", "next": "Documental"}
  ]
}
```

## Qué hace el panel

1. **Adaptadores** → enumera `/dev/dvb/adapterN` y sus frontends.
2. **Escanear** → ejecuta `dvb2m3u8 -t <tipo> --json -v …`, muestra el nº de
   servicios, la red y la **señal**.
3. **Canales** → tabla con SID, nombre, proveedor, tipo (TV/Radio) y el
   **now/next** de la EPG (EIT).
4. **Exportar M3U8** → descarga `/api/playlist.m3u8` (acepta `?base_url=…`).

## Notas

- El servidor escucha por defecto en `127.0.0.1` (solo local). Para acceso en
  red usa `--host 0.0.0.0`.
- Para sintonizar hardware real el servidor necesita permisos sobre
  `/dev/dvb/*` (normalmente hay que lanzarlo como root o con el usuario en el
  grupo `video`).

## Licencia

GPLv3 (se integra con dvb2m3u8, derivado de TVHeadend).
