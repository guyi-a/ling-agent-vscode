import type {
  ApprovalRequest,
  AskUserAnswer,
  QueuedMessage,
  QuestionRequest,
  SessionSnapshot,
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
