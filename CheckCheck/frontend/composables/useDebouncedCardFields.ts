import { useDebounceFn } from "@vueuse/core";

export type CardTextField = "name" | "text";

/**
 * Debounced writer for a card's two free-text fields, with ONE TIMER PER FIELD.
 *
 * A single shared timer is silent data loss: `useDebounceFn` keeps only the last
 * call's arguments, so a keystroke in the notes within the debounce window
 * replaces the still-pending title write and the title never reaches the server
 * (the user sees their typed title until the next delta pull snaps it back).
 * Giving each field its own timer means writes to different fields can never
 * cancel each other, while repeated writes to the SAME field still coalesce to
 * the newest value, which is the whole point of debouncing here.
 *
 * `maxWait` bounds continuous typing: an uninterrupted stream of keystrokes is
 * still flushed every `maxWait` ms instead of never.
 */
export function useDebouncedCardFields(
  write: (field: CardTextField, value: string) => void,
  wait = 500,
  maxWait = 3000
) {
  const perField: Record<CardTextField, (value: string) => void> = {
    name: useDebounceFn((value: string) => write("name", value), wait, { maxWait }),
    text: useDebounceFn((value: string) => write("text", value), wait, { maxWait }),
  };

  return (field: CardTextField, value: string) => perField[field](value);
}
