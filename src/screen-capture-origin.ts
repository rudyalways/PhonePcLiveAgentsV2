/** Base URL for screen-capture-server (override with SCREEN_CAPTURE_PORT, default 7900). */
export const SCREEN_CAPTURE_ORIGIN = `http://localhost:${process.env.SCREEN_CAPTURE_PORT ?? '7900'}`;
