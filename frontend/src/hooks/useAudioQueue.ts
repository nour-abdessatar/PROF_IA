// useAudioQueue.ts
// Plays base64-encoded MP3 audio clips sequentially as they arrive.
// Clips are indexed (sentence order) so they always play in order,
// even if they arrive out of order from the server.

import { useRef, useCallback } from "react";

export function useAudioQueue() {
  // Ordered slots: index = sentence order, value = b64 string (or undefined = not yet arrived)
  const slots   = useRef<(string | undefined)[]>([]);
  // Next slot index we're waiting to play
  const nextIdx  = useRef(0);
  // Whether a clip is currently playing
  const playing  = useRef(false);
  // Keep AudioContext alive across clips
  const ctxRef   = useRef<AudioContext | null>(null);

  const playNext = useCallback(() => {
    if (playing.current) return;

    const clip = slots.current[nextIdx.current];
    if (clip === undefined) return; // next clip hasn't arrived yet

    playing.current = true;
    const idx = nextIdx.current;
    nextIdx.current += 1;

    // Decode base64 → ArrayBuffer → AudioBuffer → play
    const binary = atob(clip);
    const bytes  = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    // Reuse AudioContext (browsers limit how many you can create)
    if (!ctxRef.current || ctxRef.current.state === "closed") {
      ctxRef.current = new AudioContext();
    }
    const ctx = ctxRef.current;

    ctx.decodeAudioData(bytes.buffer.slice(0))
      .then((buffer) => {
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.onended = () => {
          playing.current = false;
          playNext(); // chain to next clip
        };
        source.start();
      })
      .catch((err) => {
        console.error(`[audio] Failed to decode clip ${idx}:`, err);
        playing.current = false;
        playNext(); // skip broken clip
      });
  }, []);

  /**
   * Called by onAudioChunk. Slots the clip at its index position
   * and tries to advance the play queue.
   */
  const enqueue = useCallback((index: number, b64: string) => {
    slots.current[index] = b64;
    playNext();
  }, [playNext]);

  /**
   * Call this when starting a new question to clear state.
   */
  const reset = useCallback(() => {
    slots.current  = [];
    nextIdx.current = 0;
    playing.current = false;
    // Don't close AudioContext — reuse it for next question
  }, []);

  return { enqueue, reset };
}