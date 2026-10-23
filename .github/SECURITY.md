# Política de seguridad

Minerva es una herramienta local y de **solo paper trading**: no tiene credenciales de ningún exchange ni envía órdenes
reales. Aun así guarda secretos (la clave de la API de Anthropic, el token de Telegram) y expone una API con controles.

## Cómo reportar una vulnerabilidad

**No abras un issue público con los detalles de una vulnerabilidad y no pegues nunca una credencial.**

1. Usa el reporte privado de GitHub: en la pestaña **Security** del repositorio, **Report a vulnerability** (disponible
   solo si el propietario del repositorio activó el reporte privado de vulnerabilidades).
2. Si esa opción no aparece, abre un issue **sin detalles técnicos** que diga únicamente que tienes un reporte de
   seguridad y pide un canal privado.

Incluye qué ocurre, cómo reproducirlo, la versión (`git log -1 --oneline`) y el impacto que ves. No hace falta adjuntar
tus credenciales ni tu base de datos.

## Más información

El manejo de secretos, los puertos, la autenticación, qué nunca subir al repositorio y qué hacer si una credencial se
filtra están en [`docs/SEGURIDAD.md`](../docs/SEGURIDAD.md).

*English:* please do not file public issues for security problems or paste credentials. Use GitHub's private
vulnerability reporting (Security tab → Report a vulnerability) when it is enabled; otherwise open an issue that says
only that you have a security report. Details: [`docs/SEGURIDAD.md`](../docs/SEGURIDAD.md) (in Spanish).
