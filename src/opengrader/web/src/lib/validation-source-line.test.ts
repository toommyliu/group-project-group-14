import { describe, expect, it } from 'vitest';
import {
  parseValidationLocation,
  validationQuestionTarget,
  validationSourceLine,
} from './validation-source-line';
import type { OutlineGroup } from './assignment-outline';

describe('validationSourceLine', () => {
  it('reads a source line from parser errors', () => {
    expect(validationSourceLine('invalid choice at line 44: use A or B')).toBe(
      44
    );
    expect(validationSourceLine('Invalid nested question (line 9)')).toBe(9);
  });

  it('ignores messages without a positive source line', () => {
    expect(validationSourceLine('Check the rubric values.')).toBeNull();
    expect(validationSourceLine('invalid choice at line 0')).toBeNull();
    expect(validationSourceLine(undefined)).toBeNull();
  });

  it('uses structured locations for rubric errors and explicit lines for syntax errors', () => {
    const location = {
      line: 9,
      version: 'B',
      question_path: [0],
      rubric_path: [0],
    };
    expect(
      validationSourceLine(
        "question 'q1' rubric item 'Correct' must stay within -0 to +0 points",
        location
      )
    ).toBe(9);
    expect(validationSourceLine('Invalid rubric at line 12', location)).toBe(
      12
    );
    expect(parseValidationLocation(location)).toEqual(location);
    expect(
      parseValidationLocation({ ...location, question_path: [-1] })
    ).toBeNull();
  });

  it('locates reordered, added and nested visual questions by their current position', () => {
    const groups: OutlineGroup[] = [
      {
        key: 'original-version',
        label: ' Renamed ',
        questions: [
          {
            id: 'q2',
            source_line: 20,
            prompt: 'Moved to first',
            points: 10,
            correct_choice_labels: [],
            outcomes: [],
          },
          {
            id: 'q1',
            source_line: 4,
            prompt: 'Moved to second',
            points: 10,
            correct_choice_labels: [],
            outcomes: [],
            subquestions: [
              {
                id: 'q1a',
                source_line: 8,
                prompt: 'Child',
                points: 10,
                correct_choice_labels: [],
                outcomes: [],
              },
            ],
          },
          {
            id: 'new-1',
            source_line: -1,
            prompt: 'Added question',
            points: 10,
            correct_choice_labels: [],
            outcomes: [],
          },
        ],
      },
    ];
    expect(
      validationQuestionTarget(
        { version: 'Renamed', question_path: [0], rubric_path: [0], line: 80 },
        groups
      )
    ).toEqual({ version: 'original-version', line: 20, rubricPath: [0] });
    expect(
      validationQuestionTarget(
        {
          version: 'Renamed',
          question_path: [1, 0],
          rubric_path: [1, 1],
          line: 90,
        },
        groups
      )
    ).toEqual({ version: 'original-version', line: 8, rubricPath: [1, 1] });
    expect(
      validationQuestionTarget(
        { version: 'Renamed', question_path: [2] },
        groups
      )
    ).toEqual({ version: 'original-version', line: -1, rubricPath: undefined });
    expect(
      validationQuestionTarget(
        { version: 'Missing', question_path: [0] },
        groups
      )
    ).toBeNull();
  });
});
