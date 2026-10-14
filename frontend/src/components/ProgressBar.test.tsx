import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "./ProgressBar";

function barWidth(container: HTMLElement): string {
  const fill = container.querySelector(".rounded-full.transition-\\[width\\]") as HTMLElement;
  return fill.style.width;
}

describe("ProgressBar", () => {
  it("un valor de 0 da un ancho de 0%", () => {
    const { container } = render(<ProgressBar label="APROBADA" value={0} target={300} />);
    expect(barWidth(container)).toBe("0%");
  });

  it("un valor a mitad de camino da el porcentaje correcto", () => {
    const { container } = render(<ProgressBar label="APROBADA" value={150} target={300} />);
    expect(barWidth(container)).toBe("50%");
  });

  it("un valor que supera el objetivo nunca desborda mas del 100%", () => {
    const { container } = render(<ProgressBar label="APROBADA" value={450} target={300} />);
    expect(barWidth(container)).toBe("100%");
  });

  it("muestra el valor exacto como texto, no solo el ancho de la barra", () => {
    const { getByText } = render(<ProgressBar label="APROBADA" value={450} target={300} />);
    expect(getByText("450/300")).toBeInTheDocument();
  });
});
