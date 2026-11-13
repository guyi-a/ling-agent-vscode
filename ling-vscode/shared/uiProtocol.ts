import type {
  ApprovalRequest,
  AskUserAnswer,
  BackgroundTaskView,
  QueuedMessage,
  QuestionRequest,
  SessionSnapshot,
  SubagentTraceEntry,
  ToolCall,
} from "./uiTypes";

export type UiEvent =
  | {
      type: "session.snapshot";
      snapshot: SessionSnapshot;
    }
  | {
      type: "message.delta";
      messageId: string;
      role: "assistant";
      delta: string;
    }
  | {
      type: "thinking.delta";
      messageId: string;
      delta: string;
    }
  | {
      type: "thinking.completed";
      messageId: string;
    }
  | {
      type: "message.completed";
      messageId: string;
    }
  | {
      type: "tool.updated";
      tool: ToolCall;
    }
  | {
      type: "subagent.trace";
      callId: string;
      entry: SubagentTraceEntry;
    }
  | {
      type: "plan.created";
      path: string;
      sessionId: string;
    }
  | {
      type: "approval.requested";
      request: ApprovalRequest;
    }
  | {
      type: "approval.resolved";
      approvalId: string;
      approved: boolean;
    }
  | {
      type: "question.requested";
      question: QuestionRequest;
    }
  | {
      type: "question.resolved";
      callId: string;
      answers?: AskUserAnswer[];
      cancelled?: boolean;
    }
  | {
      type: "session.queue";
      queuedMessages: QueuedMessage[];
    }
  | {
      type: "background.updated";
      task: BackgroundTaskView;
    }
  | {
      type: "run.cancelled";
    }
  | {
      type: "run.failed";
      message: string;
    };

export type UiAction =
  | UiEvent
  | {
      type: "session.reset";
    };
