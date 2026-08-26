/* tracking-core.js — throttle/buffer/retry logic for GPS ride-along.

   Dependency-free factory.  Loaded before the main inline script in
   index.html and consumed by the tracking UI code.
   Exposed as window.TrackingCore for both the app and test harnesses.
 */

(function () {
  "use strict";

  /**
   * @param {Object} opts
   * @param {number} opts.intervalMs   — min ms between sends (5000 or 15000)
   * @param {number} [opts.maxBuffer]  — max buffered points (default 200)
   * @returns {TrackingCore}
   */
  function TrackingCore(opts) {
    var intervalMs = opts.intervalMs;
    var maxBuffer = opts.maxBuffer || 200;

    var _lastSendTs = -Infinity;
    var _buffer = [];

    return {
      /** Expose current interval for external reads / automated checks. */
      get intervalMs() { return intervalMs; },
      set intervalMs(v) { intervalMs = v; },

      /** Expose buffer length. */
      get bufferLength() { return _buffer.length; },

      /**
       * Decide whether a point should be sent now.
       * @param {number} nowTs — performance.now()-ish timestamp in ms
       * @returns {boolean}
       */
      shouldSend: function (nowTs) {
        if (nowTs - _lastSendTs >= intervalMs) {
          _lastSendTs = nowTs;
          return true;
        }
        return false;
      },

      /**
       * Push a failed point into the retry buffer.
       * Drops oldest if buffer exceeds maxBuffer.
       * @param {Object} point
       */
      onSendFailure: function (point) {
        _buffer.push(point);
        while (_buffer.length > maxBuffer) {
          _buffer.shift();
        }
      },

      /**
       * Drain the entire buffer (e.g. for retry).  Returns the points
       * and clears the internal buffer.
       * @returns {Array<Object>}
       */
      takeBuffer: function () {
        var out = _buffer.splice(0, _buffer.length);
        return out;
      },

      /**
       * Peek at the buffer without clearing it.
       * @returns {Array<Object>}
       */
      peekBuffer: function () {
        return _buffer.slice();
      },

      /**
       * Reset internal state (for ride end / teardown).
       */
      reset: function () {
        _lastSendTs = -Infinity;
        _buffer = [];
      }
    };
  }

  // Expose globally
  if (typeof window !== "undefined") {
    window.TrackingCore = TrackingCore;
  }
  // Also export for Node test harnesses
  if (typeof module !== "undefined" && module.exports) {
    module.exports = TrackingCore;
  }
})();
