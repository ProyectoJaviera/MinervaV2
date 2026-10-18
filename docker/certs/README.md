# Certificados raíz opcionales para la imagen

Si tu red hace **inspección TLS** (un proxy corporativo que re-firma el tráfico HTTPS), el contenedor no
confía en el certificado raíz de ese proxy aunque tu PC sí lo tenga instalado, y las llamadas a Bitunix,
CoinGecko, Anthropic o Telegram fallan **solo dentro del contenedor** con `CERTIFICATE_VERIFY_FAILED`
(`app/core/http.py` usa `truststore`, que lee el almacén del sistema operativo *de la imagen*).

Para arreglarlo:

1. Exporta el certificado raíz de tu red en formato PEM (Base-64) y guárdalo aquí con extensión **`.crt`**,
   por ejemplo `docker/certs/mi-red.crt`.
2. Reconstruye la imagen: `docker compose build`. El `Dockerfile` copia todo `*.crt` de esta carpeta al
   almacén de confianza (`update-ca-certificates`). Sin archivos `.crt` no hace nada.

Los `.crt` de esta carpeta **no se versionan** (`.gitignore`): son específicos de tu red. Un certificado raíz
es público (no es una clave privada), pero no tiene sentido llevarlo a otro PC. **Nunca** guardes aquí una
clave privada (`.key`).
