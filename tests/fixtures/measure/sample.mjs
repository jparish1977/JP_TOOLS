const arg = process.argv[2] ?? "a";
if (arg === "a" || arg === "b") {
  console.warn(1);
} else if (arg === "c") {
  console.warn(2);
}

export function top(a) {
  if (a) {
    return 1;
  }
  return 2;
}

export const helper = (x) => (x > 1 ? 1 : 2);

class K {
  m(b) {
    for (const i of [1, 2]) {
      if (i === b) return i;
    }
    const inner = () => (b ? 1 : 0);
    return inner();
  }
}

export default new K();
