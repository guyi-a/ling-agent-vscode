import React from "react";
import { Box, Text } from "ink";
import { lexer, type Token } from "marked";

type AnyToken = Token & Record<string, unknown>;

function asToken(value: unknown): AnyToken | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  return value as AnyToken;
}

function charWidth(char: string): number {
  const cp = char.codePointAt(0) ?? 0;
  return cp >= 0x1100 &&
    (cp <= 0x115f ||
      cp === 0x2329 ||
      cp === 0x232a ||
      (cp >= 0x2e80 && cp <= 0xa4cf) ||
      (cp >= 0xac00 && cp <= 0xd7a3) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0xfe10 && cp <= 0xfe19) ||
      (cp >= 0xfe30 && cp <= 0xfe6f) ||
      (cp >= 0xff00 && cp <= 0xff60) ||
      (cp >= 0xffe0 && cp <= 0xffe6) ||
      (cp >= 0x1f300 && cp <= 0x1f64f) ||
      (cp >= 0x1f900 && cp <= 0x1f9ff) ||
      (cp >= 0x20000 && cp <= 0x3fffd))
    ? 2
    : 1;
}

function displayWidth(value: string): number {
  let width = 0;
  for (const char of value) {
    width += charWidth(char);
  }
  return width;
}

function wrapPlain(value: string, width: number): string[] {
  const lines: string[] = [];
  let line = "";
  let lineWidth = 0;
  let lastSpace = -1;
  for (const char of value) {
    if (char === "\n") {
      lines.push(line.trimEnd());
      line = "";
      lineWidth = 0;
      lastSpace = -1;
      continue;
    }
    const w = charWidth(char);
    if (lineWidth + w > width && line.length > 0) {
      const cut = lastSpace >= 0 ? lastSpace : line.length;
      const head = line.slice(0, cut).trimEnd();
      const tail = line.slice(cut).trimStart();
      if (head) {
        lines.push(head);
      }
      line = tail;
      lineWidth = displayWidth(line);
      lastSpace = -1;
    }
    line += char;
    lineWidth += w;
    if (char === " ") {
      lastSpace = line.length - 1;
    }
  }
  if (line.trimEnd()) {
    lines.push(line.trimEnd());
  }
  return lines;
}

function plainInline(tokens: unknown): string {
  if (!Array.isArray(tokens)) {
    return "";
  }
  let result = "";
  for (const raw of tokens) {
    const token = asToken(raw);
    if (!token) {
      continue;
    }
    const type = String(token.type);
    const text = typeof token.text === "string" ? token.text : "";
    switch (type) {
      case "text":
      case "escape":
      case "codespan":
        result += token.tokens
          ? plainInline(token.tokens)
          : text;
        break;
      case "br":
        result += "\n";
        break;
      case "strong":
      case "em":
      case "del":
        result += plainInline(token.tokens) || text;
        break;
      case "link":
        result += plainInline(token.tokens) || text;
        break;
      case "image":
        result += `[image: ${text || "image"}]`;
        break;
      default:
        result += token.tokens
          ? plainInline(token.tokens)
          : text || String(token.raw ?? "");
    }
  }
  return result;
}

function renderInline(
  tokens: unknown,
  keyPrefix: string,
): React.ReactNode[] {
  if (!Array.isArray(tokens)) {
    return [];
  }
  return tokens.map((raw, index) => {
    const token = asToken(raw);
    if (!token) {
      return null;
    }
    const key = `${keyPrefix}-${index}`;
    const type = String(token.type);
    const text = typeof token.text === "string" ? token.text : "";
    switch (type) {
      case "text":
        return token.tokens
          ? <React.Fragment key={key}>{renderInline(token.tokens, key)}</React.Fragment>
          : <React.Fragment key={key}>{text}</React.Fragment>;
      case "escape":
        return <React.Fragment key={key}>{text}</React.Fragment>;
      case "strong":
        return (
          <Text key={key} bold>
            {renderInline(token.tokens, key)}
          </Text>
        );
      case "em":
        return (
          <Text key={key} italic>
            {renderInline(token.tokens, key)}
          </Text>
        );
      case "del":
        return (
          <Text key={key} strikethrough>
            {renderInline(token.tokens, key)}
          </Text>
        );
      case "codespan":
        return (
          <Text key={key} color="yellow">
            {text}
          </Text>
        );
      case "br":
        return <React.Fragment key={key}>{"\n"}</React.Fragment>;
      case "link": {
        const href = typeof token.href === "string" ? token.href : "";
        const label = token.tokens
          ? renderInline(token.tokens, key)
          : text || href;
        return (
          <React.Fragment key={key}>
            <Text color="cyan" underline>
              {label}
            </Text>
            {href && href !== text ? (
              <Text dimColor>{` (${href})`}</Text>
            ) : null}
          </React.Fragment>
        );
      }
      case "image": {
        const href = typeof token.href === "string" ? token.href : "";
        return (
          <Text key={key} dimColor>
            {`[image: ${text || "image"}]${href ? ` ${href}` : ""}`}
          </Text>
        );
      }
      default:
        return token.tokens
          ? <React.Fragment key={key}>{renderInline(token.tokens, key)}</React.Fragment>
          : <React.Fragment key={key}>{text || String(token.raw ?? "")}</React.Fragment>;
    }
  });
}

function renderListItem(
  item: AnyToken,
  index: number,
  ordered: boolean,
  keyPrefix: string,
  width: number,
): React.ReactNode {
  const number =
    typeof item.number === "number" ? item.number : index + 1;
  const marker = ordered ? `${number}.` : "•";
  const checkbox =
    item.task && typeof item.checked === "boolean"
      ? item.checked
        ? " [x]"
        : " [ ]"
      : "";
  const prefix = `${marker}${checkbox} `;
  const continuation = " ".repeat(displayWidth(prefix));
  const nested = Array.isArray(item.tokens)
    ? item.tokens.filter((token) => asToken(token)?.type === "list")
    : [];
  const inlineTokens = Array.isArray(item.tokens)
    ? item.tokens.filter((token) => asToken(token)?.type !== "list")
    : [];
  const bodyWidth = Math.max(8, width - displayWidth(prefix));
  const lines = wrapPlain(
    plainInline(inlineTokens).trim(),
    bodyWidth,
  );
  return (
    <Box key={`${keyPrefix}-${index}`} flexDirection="column">
      {(lines.length > 0 ? lines : [""]).map((line, lineIndex) => (
        <Text key={`${keyPrefix}-${index}-line-${lineIndex}`}>
          <Text color="cyan">
            {lineIndex === 0 ? prefix : continuation}
          </Text>
          {line}
        </Text>
      ))}
      {nested.map((token, nestedIndex) =>
        renderBlock(
          asToken(token),
          `${keyPrefix}-${index}-nested-${nestedIndex}`,
          1,
          Math.max(8, width - 2),
        ),
      )}
    </Box>
  );
}

function renderBlock(
  token: AnyToken | null,
  key: string,
  indent = 0,
  width = 80,
): React.ReactNode {
  if (!token) {
    return null;
  }
  const type = String(token.type);
  const text = typeof token.text === "string" ? token.text : "";
  const paddingLeft = indent > 0 ? indent : undefined;
  switch (type) {
    case "space":
      return <Text key={key}> </Text>;
    case "heading": {
      const depth = Number(token.depth ?? 1);
      const body = token.tokens
        ? renderInline(token.tokens, key)
        : text;
      return (
        <Text
          key={key}
          bold
          underline={depth === 1}
          color={depth <= 2 ? "cyan" : undefined}
        >
          {body}
        </Text>
      );
    }
    case "paragraph":
      return (
        <Box key={key} paddingLeft={paddingLeft}>
          <Text>
            {token.tokens
              ? renderInline(token.tokens, key)
              : text}
          </Text>
        </Box>
      );
    case "text":
      return (
        <Text key={key}>
          {token.tokens ? renderInline(token.tokens, key) : text}
        </Text>
      );
    case "code": {
      const lang = typeof token.lang === "string" ? token.lang : "";
      if (/type\s*=\s*["']renderer["']/i.test(lang)) {
        const lineCount = text.split("\n").length;
        const unit = lineCount === 1 ? "line" : "lines";
        return (
          <Text key={key} color="cyan" dimColor>
            {`[inline visualization · ${lineCount} ${unit} · open in VS Code to view]`}
          </Text>
        );
      }
      return (
        <Box
          key={key}
          flexDirection="column"
          paddingLeft={paddingLeft ?? 1}
        >
          <Text dimColor>{`\`\`\`${lang}`}</Text>
          <Text color="gray">{text}</Text>
          <Text dimColor>```</Text>
        </Box>
      );
    }
    case "blockquote":
      return (
        <Box key={key} paddingLeft={paddingLeft}>
          <Text dimColor>│ </Text>
          <Text dimColor>
            {token.tokens
              ? renderInline(token.tokens, key)
              : text}
          </Text>
        </Box>
      );
    case "list": {
      const items = Array.isArray(token.items) ? token.items : [];
      return (
        <Box
          key={key}
          flexDirection="column"
          paddingLeft={paddingLeft}
        >
          {items.map((item, index) =>
            renderListItem(
              asToken(item) ?? ({} as AnyToken),
              index,
              Boolean(token.ordered),
              `${key}-${index}`,
              Math.max(8, width - (paddingLeft ?? 0)),
            ),
          )}
        </Box>
      );
    }
    case "hr":
      return <Text key={key} dimColor>────────</Text>;
    case "table": {
      const header = Array.isArray(token.header)
        ? token.header
            .map((cell) =>
              typeof cell === "object" && cell && "text" in cell
                ? String((cell as { text?: unknown }).text ?? "")
                : String(cell ?? ""),
            )
            .join(" | ")
        : "";
      const rows = Array.isArray(token.rows) ? token.rows : [];
      return (
        <Box key={key} flexDirection="column">
          {header ? <Text bold>{header}</Text> : null}
          {rows.map((row, index) => (
            <Text key={`${key}-row-${index}`}>
              {Array.isArray(row)
                ? row
                    .map((cell) =>
                      typeof cell === "object" && cell && "text" in cell
                        ? String((cell as { text?: unknown }).text ?? "")
                        : String(cell ?? ""),
                    )
                    .join(" | ")
                : String(row ?? "")}
            </Text>
          ))}
        </Box>
      );
    }
    case "html":
      return <Text key={key}>{text}</Text>;
    default:
      return (
        <Text key={key}>
          {token.tokens
            ? renderInline(token.tokens, key)
            : text || String(token.raw ?? "")}
        </Text>
      );
  }
}

export function Markdown({
  text,
  width = 80,
}: {
  text: string;
  width?: number;
}): React.ReactElement {
  const tokens = lexer(text, { gfm: true }) as unknown as AnyToken[];
  return (
    <Box flexDirection="column">
      {tokens.map((token, index) =>
        renderBlock(token, `md-${index}`, 0, width),
      )}
    </Box>
  );
}
