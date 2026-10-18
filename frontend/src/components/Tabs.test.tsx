import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Tabs, tabId, tabPanelId } from "./Tabs";

const TABS = [
  { id: "a", label: "Alfa" },
  { id: "b", label: "Beta" },
  { id: "c", label: "Gamma" },
];

function Harness({ initial = "a" }: { initial?: string }) {
  const [active, setActive] = useState(initial);
  return (
    <>
      <Tabs tabs={TABS} active={active} onChange={setActive} />
      <div role="tabpanel" id={tabPanelId(active)} aria-labelledby={tabId(active)}>
        contenido de {active}
      </div>
    </>
  );
}

const tab = (name: string) => screen.getByRole("tab", { name });

describe("Tabs", () => {
  it("expone el patron de pestanas: lista, pestanas, seleccion y panel enlazado", () => {
    render(<Harness />);

    expect(screen.getByRole("tablist")).toHaveAccessibleName("Secciones del dashboard");
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(tab("Alfa")).toHaveAttribute("aria-selected", "true");
    expect(tab("Beta")).toHaveAttribute("aria-selected", "false");
    expect(tab("Alfa")).toHaveAttribute("aria-controls", "panel-a");
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("id", "panel-a");
    expect(panel).toHaveAccessibleName("Alfa");
  });

  it("solo la pestana activa entra en el orden de tabulacion", () => {
    render(<Harness />);

    expect(tab("Alfa")).toHaveAttribute("tabindex", "0");
    expect(tab("Beta")).toHaveAttribute("tabindex", "-1");
    expect(tab("Gamma")).toHaveAttribute("tabindex", "-1");
  });

  it("hacer clic cambia de pestana y de panel", () => {
    render(<Harness />);

    fireEvent.click(tab("Beta"));

    expect(tab("Beta")).toHaveAttribute("aria-selected", "true");
    expect(tab("Alfa")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("contenido de b");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Beta");
  });

  it("las flechas mueven la seleccion y el foco, y dan la vuelta en los extremos", () => {
    render(<Harness />);

    fireEvent.keyDown(tab("Alfa"), { key: "ArrowRight" });
    expect(tab("Beta")).toHaveAttribute("aria-selected", "true");
    expect(tab("Beta")).toHaveFocus();

    fireEvent.keyDown(tab("Beta"), { key: "ArrowRight" });
    fireEvent.keyDown(tab("Gamma"), { key: "ArrowRight" }); // del ultimo al primero
    expect(tab("Alfa")).toHaveAttribute("aria-selected", "true");
    expect(tab("Alfa")).toHaveFocus();

    fireEvent.keyDown(tab("Alfa"), { key: "ArrowLeft" }); // del primero al ultimo
    expect(tab("Gamma")).toHaveAttribute("aria-selected", "true");
    expect(tab("Gamma")).toHaveFocus();
  });

  it("Inicio y Fin saltan a los extremos", () => {
    render(<Harness initial="b" />);

    fireEvent.keyDown(tab("Beta"), { key: "End" });
    expect(tab("Gamma")).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tab("Gamma"), { key: "Home" });
    expect(tab("Alfa")).toHaveAttribute("aria-selected", "true");
    expect(tab("Alfa")).toHaveFocus();
  });

  it("otras teclas no cambian nada", () => {
    render(<Harness />);

    fireEvent.keyDown(tab("Alfa"), { key: "ArrowDown" });
    fireEvent.keyDown(tab("Alfa"), { key: "Enter" });
    fireEvent.keyDown(tab("Alfa"), { key: "x" });

    expect(tab("Alfa")).toHaveAttribute("aria-selected", "true");
  });
});
