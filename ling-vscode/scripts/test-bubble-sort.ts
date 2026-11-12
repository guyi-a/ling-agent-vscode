import assert from "node:assert/strict";
import { bubbleSort } from "../shared/bubbleSort";

function main(): void {
  assert.deepEqual(bubbleSort([3, 1, 2]), [1, 2, 3]);
  assert.deepEqual(bubbleSort([5]), [5]);
  assert.deepEqual(bubbleSort([]), []);
  assert.deepEqual(bubbleSort([1, 2, 3, 4]), [1, 2, 3, 4]);
  assert.deepEqual(bubbleSort([4, 3, 2, 1]), [1, 2, 3, 4]);
  assert.deepEqual(bubbleSort([-2, 10, 0, -7]), [-7, -2, 0, 10]);
  assert.deepEqual(bubbleSort(["pear", "apple", "fig"]), [
    "apple",
    "fig",
    "pear",
  ]);

  const input = [3, 1, 2];
  const sorted = bubbleSort(input);
  assert.deepEqual(input, [3, 1, 2], "入参不应被修改");
  assert.notEqual(sorted, input, "应返回新数组");

  assert.deepEqual(
    bubbleSort([{ n: 2 }, { n: 1 }], (a, b) => a.n - b.n),
    [{ n: 1 }, { n: 2 }],
  );

  const stable = bubbleSort(
    [
      { id: "a", rank: 1 },
      { id: "b", rank: 1 },
      { id: "c", rank: 0 },
    ],
    (a, b) => a.rank - b.rank,
  );
  assert.deepEqual(
    stable.map((item) => item.id),
    ["c", "a", "b"],
    "相同 rank 应保持原有顺序（稳定）",
  );

  console.log("bubbleSort: all assertions passed");
}

main();
