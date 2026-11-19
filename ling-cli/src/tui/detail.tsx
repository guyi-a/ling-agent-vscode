import React from "react";
import { Box, Text } from "ink";
import { diffLines } from "diff";

import type { ApprovalRequest, TimelineEntry } from "../types.js";

interface DiffRow {
  type: string;
  oldLine?: number;
  newLine?: number;
  text?: string;
}

function splitLines(value: string): string[] {
  const lines = value.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines;
}

function buildDiffRows(oldText: string, newText: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldLine = 1;
  let newLine = 1;
  for (const part of diffLines(oldText, newText)) {
    for (const line of splitLines(part.value)) {
      if (part.added) {
        rows.push({ type: "add", newLine, text: line });
        newLine += 1;
      } else if (part.removed) {
        rows.push({ type: "remove", oldLine, text: line });
        oldLine += 1;
      } else {
        rows.push({
          type: "context",
          oldLine,
          newLine,
          text: line,
        });
        oldLine += 1;
        newLine += 1;
      }
    }
  }
  return rows;
}

function DiffRows({
  rows,
  truncated = false,
  limit = 200,
}: {
  rows: DiffRow[];
  truncated?: boolean;
  limit?: number;
}) {
  const shown = rows.slice(0, limit);
  return (
    <Box flexDirection="column">
      {shown.map((row, index) => {
        const isAdd = row.type === "add";
        const isRemove = row.type === "remove";
        const prefix = isAdd ? "+" : isRemove ? "-" : " ";
        return (
          <Text
            key={`${row.type}-${row.oldLine ?? ""}-${row.newLine ?? ""}-${index}`}
            color={isAdd ? "green" : isRemove ? "red" : undefined}
            dimColor={!isAdd && !isRemove}
          >
            {`${prefix} ${row.text ?? ""}`}
          </Text>
        );
      })}
      {rows.length > limit || truncated ? (
        <Text dimColor>
          {`[... truncated${rows.length > limit ? `, ${rows.length - limit} more rows` : ""} ...]`}
        </Text>
      ) : null}
    </Box>
  );
}

function CodeLines({
  value,
  limit = 40,
  color,
}: {
  value: string;
  limit?: number;
  color?: string;
}) {
  const lines = splitLines(value);
  const shown = lines.slice(0, limit);
  return (
    <Box flexDirection="column">
      {shown.map((line, index) => (
        <Text key={`line-${index}`} color={color} dimColor={!color}>
          {line || " "}
        </Text>
      ))}
      {lines.length > limit ? (
        <Text dimColor>
          {`[... truncated, ${lines.length - limit} more lines ...]`}
        </Text>
      ) : null}
    </Box>
  );
}

export function ToolDetail({
  entry,
}: {
  entry: TimelineEntry;
}): React.ReactElement | null {
  const tool = entry.tool;
  if (!tool) {
    return null;
  }
  const detail = (tool.detail ?? {}) as Record<string, unknown>;
  const kind = String(detail.kind ?? "");
  if (kind === "diff") {
    const rows = Array.isArray(detail.rows)
      ? (detail.rows as DiffRow[])
      : [];
    return (
      <Box flexDirection="column" paddingLeft={3} marginTop={0}>
        <DiffRows
          rows={rows}
          truncated={Boolean(detail.truncated)}
        />
      </Box>
    );
  }
  if (kind === "io") {
    const input = String(detail.input ?? "");
    const output = String(detail.output ?? "");
    return (
      <Box flexDirection="column" paddingLeft={3}>
        {input ? (
          <>
            <Text dimColor>input</Text>
            <CodeLines value={input} limit={20} />
          </>
        ) : null}
        {output ? (
          <>
            <Text dimColor>output</Text>
            <CodeLines
              value={output}
              limit={40}
              color={tool.status === "Failed" ? "red" : undefined}
            />
          </>
        ) : null}
      </Box>
    );
  }
  if (Array.isArray(tool.details) && tool.details.length > 0) {
    return (
      <Box flexDirection="column" paddingLeft={3}>
        {tool.details.map((line, index) => (
          <Text key={`detail-${index}`} dimColor>
            {String(line)}
          </Text>
        ))}
      </Box>
    );
  }
  return null;
}

export function ApprovalDetail({
  approval,
}: {
  approval: ApprovalRequest;
}): React.ReactElement | null {
  const args = (approval.arguments ?? {}) as Record<string, unknown>;
  if (approval.tool === "run_command") {
    const command = String(args.command ?? "");
    return command ? (
      <Box
        flexDirection="column"
        paddingLeft={1}
        marginTop={0}
      >
        <Text dimColor>command</Text>
        <CodeLines value={command} limit={20} color="yellow" />
      </Box>
    ) : null;
  }
  if (approval.tool === "write_file") {
    const content = String(args.content ?? "");
    return content ? (
      <Box flexDirection="column" paddingLeft={1}>
        <Text dimColor>content</Text>
        <DiffRows rows={buildDiffRows("", content)} limit={80} />
      </Box>
    ) : null;
  }
  if (approval.tool === "edit_file") {
    const oldText = String(args.old_text ?? "");
    const newText = String(args.new_text ?? "");
    if (oldText || newText) {
      return (
        <Box flexDirection="column" paddingLeft={1}>
          <Text dimColor>diff</Text>
          <DiffRows
            rows={buildDiffRows(oldText, newText)}
            limit={80}
          />
        </Box>
      );
    }
  }
  const serialized = JSON.stringify(args, null, 2);
  return serialized !== "{}" ? (
    <Box flexDirection="column" paddingLeft={1}>
      <CodeLines value={serialized} limit={20} />
    </Box>
  ) : null;
}
