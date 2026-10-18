import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

function setup(overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      title="Pausar medicion"
      warning="ATENCION: pausar DETIENE LA MEDICION."
      phrase="PAUSAR"
      confirmLabel="Pausar ahora"
      busy={false}
      error={null}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />
  );
  return {
    onConfirm,
    onCancel,
    input: screen.getByLabelText(/Para confirmar escribe/),
    confirm: screen.getByRole("button", { name: /Pausar ahora|Ejecutando/ }),
  };
}

describe("ConfirmDialog", () => {
  it("es un dialogo modal con titulo y advertencia asociados, y el foco en el campo", () => {
    const { input } = setup();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Pausar medicion");
    expect(dialog).toHaveAccessibleDescription("ATENCION: pausar DETIENE LA MEDICION.");
    expect(input).toHaveFocus();
    expect(screen.getByText("PAUSAR")).toBeInTheDocument();
  });

  it("no deja confirmar hasta escribir la palabra exacta", () => {
    const { input, confirm, onConfirm } = setup();
    expect(confirm).toBeDisabled();

    for (const wrong of ["", "pausa", "PAUSARR", "PAUS AR", "REANUDAR"]) {
      fireEvent.change(input, { target: { value: wrong } });
      expect(confirm).toBeDisabled();
    }
    fireEvent.submit(screen.getByRole("dialog")); // ni siquiera con Enter
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("acepta la palabra sin importar mayusculas ni espacios laterales, y envia la exacta", () => {
    const { input, confirm, onConfirm } = setup();

    fireEvent.change(input, { target: { value: "  pausar " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("PAUSAR");
  });

  it("Enter dentro del campo (submit) confirma solo si la palabra coincide", () => {
    const { input, onConfirm } = setup();

    fireEvent.change(input, { target: { value: "PAUSAR" } });
    fireEvent.submit(screen.getByRole("dialog"));

    expect(onConfirm).toHaveBeenCalledWith("PAUSAR");
  });

  it("Cancelar y Escape avisan al padre y no confirman", () => {
    const { onCancel, onConfirm } = setup();

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("otras teclas no cancelan", () => {
    const { onCancel } = setup();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "a" });

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("ocupado: bloquea campo, botones, Escape y el envio", () => {
    const { input, confirm, onCancel, onConfirm } = setup({ busy: true });

    expect(confirm).toHaveTextContent("Ejecutando...");
    expect(confirm).toBeDisabled();
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("muestra el error del servidor como alerta sin cerrarse", () => {
    setup({ error: "Para confirmar escribe exactamente PAUSAR." });

    expect(screen.getByRole("alert")).toHaveTextContent("Para confirmar escribe exactamente PAUSAR.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("sin error no hay alerta", () => {
    setup();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
