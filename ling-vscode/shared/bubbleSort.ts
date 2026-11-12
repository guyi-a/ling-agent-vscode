/**
 * 稳定冒泡排序：不修改入参，返回排好序的新数组。
 * 不传比较器时按数值（number）或字符串字典序比较。
 */
export function bubbleSort<T>(
  items: readonly T[],
  compare?: (a: T, b: T) => number,
): T[] {
  const order: (a: T, b: T) => number = compare ?? defaultCompare;
  const result = items.slice();

  for (let end = result.length - 1; end > 0; end -= 1) {
    let swapped = false;
    for (let i = 0; i < end; i += 1) {
      if (order(result[i], result[i + 1]) > 0) {
        const previous = result[i];
        result[i] = result[i + 1];
        result[i + 1] = previous;
        swapped = true;
      }
    }
    // 一轮下来没有交换，说明已经有序，提前退出。
    if (!swapped) {
      break;
    }
  }

  return result;
}

function defaultCompare(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") {
    return a - b;
  }
  const left = String(a);
  const right = String(b);
  if (left < right) {
    return -1;
  }
  if (left > right) {
    return 1;
  }
  return 0;
}
