import { useEffect, useMemo, useState } from "react";
import type { AskUserAnswer, QuestionRequest } from "../types";

interface QuestionTakeoverProps {
  request: QuestionRequest;
  index: number;
  total: number;
  onAnswer(answers: AskUserAnswer[]): void;
  onCancelRun(): void;
}

interface DraftAnswer {
  selected: string[];
  custom: string;
}

export function QuestionTakeover({
  request,
  index,
  total,
  onAnswer,
  onCancelRun,
}: QuestionTakeoverProps) {
  const [drafts, setDrafts] = useState<Record<string, DraftAnswer>>({});

  useEffect(() => {
    setDrafts({});
  }, [request.callId]);

  const complete = useMemo(
    () =>
      request.questions.every((question) => {
        const draft = drafts[question.id];
        return Boolean(
          draft &&
            (draft.selected.length > 0 || draft.custom.trim().length > 0),
        );
      }),
    [drafts, request.questions],
  );

  function toggle(
    questionId: string,
    label: string,
    multiSelect: boolean,
  ): void {
    setDrafts((current) => {
      const draft = current[questionId] ?? {
        selected: [],
        custom: "",
      };
      const selected = multiSelect
        ? draft.selected.includes(label)
          ? draft.selected.filter((item) => item !== label)
          : [...draft.selected, label]
        : [label];
      return {
        ...current,
        [questionId]: { ...draft, selected },
      };
    });
  }

  function setCustom(questionId: string, custom: string): void {
    setDrafts((current) => ({
      ...current,
      [questionId]: {
        selected: current[questionId]?.selected ?? [],
        custom,
      },
    }));
  }

  function submit(): void {
    const answers = request.questions.map((question) => {
      const draft = drafts[question.id];
      const custom = draft?.custom.trim() ?? "";
      return {
        id: question.id,
        selected: draft?.selected ?? [],
        ...(custom ? { custom } : {}),
      };
    });
    onAnswer(answers);
  }

  return (
    <section className="approval-takeover question-takeover" aria-label="Question">
      <div className="approval-heading">
        <div>
          <p className="approval-kicker">Question</p>
          <h2>{request.questions[0]?.header ?? "Input needed"}</h2>
        </div>
        {total > 1 ? (
          <span className="approval-queue">
            {index + 1} / {total}
          </span>
        ) : null}
      </div>

      <div className="question-list">
        {request.questions.map((question, questionIndex) => {
          const draft = drafts[question.id];
          const hasOptions = question.options.length > 0;
          return (
            <fieldset className="question-block" key={question.id}>
              <legend>
                {question.header ? (
                  <span className="question-header">
                    {question.header}
                  </span>
                ) : null}
                {question.question}
              </legend>
              {hasOptions ? (
                <div className="question-options">
                  {question.options.map((option) => {
                    const checked =
                      draft?.selected.includes(option.label) ?? false;
                    return (
                      <label
                        className={`question-option${
                          checked ? " question-option--selected" : ""
                        }`}
                        key={option.label}
                      >
                        <input
                          type={question.multiSelect ? "checkbox" : "radio"}
                          name={`question-${question.id}`}
                          checked={checked}
                          onChange={() =>
                            toggle(
                              question.id,
                              option.label,
                              question.multiSelect === true,
                            )
                          }
                        />
                        <span>
                          <strong>{option.label}</strong>
                          {option.description ? (
                            <small>{option.description}</small>
                          ) : null}
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : (
                <input
                  className="question-custom"
                  type="text"
                  value={draft?.custom ?? ""}
                  placeholder="Type your answer"
                  autoFocus={questionIndex === 0}
                  onChange={(event) =>
                    setCustom(question.id, event.target.value)
                  }
                />
              )}
              {hasOptions ? (
                <input
                  className="question-custom"
                  type="text"
                  value={draft?.custom ?? ""}
                  placeholder="Other (optional)"
                  onChange={(event) =>
                    setCustom(question.id, event.target.value)
                  }
                />
              ) : null}
            </fieldset>
          );
        })}
      </div>

      <div className="approval-actions">
        <button
          className="button button--secondary"
          type="button"
          onClick={onCancelRun}
        >
          Cancel run
        </button>
        <button
          className="button button--primary"
          type="button"
          disabled={!complete}
          onClick={submit}
        >
          Submit
        </button>
      </div>
    </section>
  );
}
