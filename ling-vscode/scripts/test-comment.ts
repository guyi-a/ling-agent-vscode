import assert from "node:assert/strict";
import {
  formatCommentBlock,
  insertAtLine,
} from "../src/comment/format";
import { isCommentGeneration } from "../shared/commentTypes";

const generation = {
  comment: '"""Adds two numbers."""',
  placement: { line: 9, indent: "    " },
};
assert.equal(isCommentGeneration(generation), true);
assert.equal(
  isCommentGeneration({
    comment: "",
    placement: { line: 0, indent: "" },
  }),
  false,
);
assert.equal(
  isCommentGeneration({
    comment: '"""Adds two numbers."""',
    placement: { line: -1, indent: "" },
  }),
  false,
);
assert.equal(
  isCommentGeneration({
    comment: '"""Adds two numbers."""',
    placement: { line: 0, indent: 4 },
  }),
  false,
);
assert.equal(isCommentGeneration(null), false);
assert.equal(isCommentGeneration({ comment: "x" }), false);

assert.equal(
  formatCommentBlock(
    '"""Line one.\n\nLine two."""',
    "    ",
    "\n",
  ),
  '    """Line one.\n\n    Line two."""\n',
);
assert.equal(
  formatCommentBlock('"""Method."""', "        ", "\r\n"),
  '        """Method."""\r\n',
);

assert.equal(
  insertAtLine(
    "def calc():\n    return 1\n",
    1,
    '    """Adds one."""\n',
  ),
  'def calc():\n    """Adds one."""\n    return 1\n',
);
assert.equal(
  insertAtLine(
    "def calc():\r\n    return 1\r\n",
    1,
    '    """Adds one."""\r\n',
  ),
  'def calc():\r\n    """Adds one."""\r\n    return 1\r\n',
);
assert.equal(
  insertAtLine("def calc():\n    return 1", 1, '    """x"""\n'),
  'def calc():\n    """x"""\n    return 1',
);

console.log("comment tests passed");
