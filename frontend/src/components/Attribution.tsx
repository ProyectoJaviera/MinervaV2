// Atribucion obligatoria de CoinGecko (CLAUDE.md; docs/FASE2_PLAN.md seccion D). Verificado el
// 2026-10-18 en la guia oficial (brand.coingecko.com/resources/attribution-guide): "Data provided by
// CoinGecko" es una frase aceptada, enlazada a https://www.coingecko.com, en un lugar visible y
// cerca de los datos. Los terminos de la API piden letra legible de al menos 10 px (text-xs = 12 px).
export const COINGECKO_TEXT = "Data provided by CoinGecko";
export const COINGECKO_URL = "https://www.coingecko.com";

export function CoinGeckoAttribution({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs text-gray-500 dark:text-gray-400 ${className}`}>
      <a
        href={COINGECKO_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="underline hover:text-gray-800 dark:hover:text-gray-200"
      >
        {COINGECKO_TEXT}
      </a>
    </p>
  );
}
