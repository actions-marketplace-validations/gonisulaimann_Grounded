import { internals } from "./internals.js";

export const {
  optionCategories,
  fastGlob: glob,
  ...rest
} = internals;
export const [first, second = 2] = [1];

const cache = new WeakMap();
function attach() {}

export {
  attach,
  // Shared with src/use.js, will remove later
  cache,
};
