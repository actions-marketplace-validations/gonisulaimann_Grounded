import axios from "axios";
import { helper } from "./real.js";

const TIMEOUT = 30;

/**
 * Pings. Uses `helper()` and `axios.get()` under the hood.
 *
 * @param {string} url the target
 */
export async function ping(url) {
  // Calls `ghost_endpoint()` on failure.
  // timeout is 60s for the request.
  // See line 99 for the backoff policy.
  return helper(await axios.get(url));
}
