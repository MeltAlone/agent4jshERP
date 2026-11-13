type ComposerKey = {
  key: string;
  shiftKey: boolean;
  keyCode: number;
  nativeEvent: { isComposing: boolean };
};

// IME candidate confirmation can report Enter or the legacy composition code.
export function submitsMessage(event: ComposerKey): boolean {
  return event.key === "Enter" && !event.shiftKey &&
    !event.nativeEvent.isComposing && event.keyCode !== 229;
}
