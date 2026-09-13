// Generated from FastAPI. Run pnpm generate:api to update.
export interface paths {
    "/assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Assignments */
        get: operations["list_assignments_assignments_get"];
        put?: never;
        /**
         * Create Assignment
         * @description Publish complete assignment files before adding the in-memory snapshot.
         */
        post: operations["create_assignment_assignments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Assignment */
        post: operations["preview_assignment_assignments_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/preview/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Assignment Pdf */
        post: operations["preview_assignment_pdf_assignments_preview_pdf_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/source": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Assignment Source */
        get: operations["get_assignment_source_assignments__assignment_id__source_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Assignment */
        put: operations["update_assignment_assignments__assignment_id__put"];
        post?: never;
        /** Delete Assignment */
        delete: operations["delete_assignment_assignments__assignment_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/qa": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Assignment Qa */
        get: operations["get_assignment_qa_assignments__assignment_id__qa_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Assignment Pdf */
        get: operations["get_assignment_pdf_assignments__assignment_id__pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/submissions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Submissions */
        get: operations["list_submissions_assignments__assignment_id__submissions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Questions
         * @description List the selected submission's answer-key version; default to the first.
         */
        get: operations["list_questions_assignments__assignment_id__questions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Answer */
        get: operations["get_answer_assignments__assignment_id__submissions__submission_id__answers__question_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}/image": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Answer Image */
        get: operations["get_answer_image_assignments__assignment_id__submissions__submission_id__answers__question_id__image_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Answer */
        Answer: {
            /** Submission Id */
            submission_id: number;
            /** Version */
            version: string | null;
            question: components["schemas"]["Question"];
            /** Response Text */
            response_text: string;
            /** Response Image Url */
            response_image_url: string | null;
            /** Response Image Width */
            response_image_width: number | null;
            /** Response Image Height */
            response_image_height: number | null;
            /** Points Awarded */
            points_awarded: number | null;
            /** Rubric Labels */
            rubric_labels: string[];
            /** Analysis */
            analysis: string;
        };
        /** Assignment */
        Assignment: {
            /** Id */
            id: string;
            /** Title */
            title: string;
        };
        /** AssignmentInput */
        AssignmentInput: {
            /** Qa Markdown */
            qa_markdown: string;
        };
        /** AssignmentPDFInput */
        AssignmentPDFInput: {
            /** Qa Markdown */
            qa_markdown: string;
            /** Version */
            version?: string | null;
        };
        /** AssignmentUpdate */
        AssignmentUpdate: {
            /** Qa Markdown */
            qa_markdown: string;
            /** Expected Qa Markdown */
            expected_qa_markdown: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** QAChoice */
        QAChoice: {
            /** Label */
            label: string;
            /** Text */
            text: string;
        };
        /** QADocument */
        QADocument: {
            /** Questions */
            questions: components["schemas"]["QAQuestion"][];
            /** Title */
            title?: string | null;
            /** Instructions */
            instructions?: string | null;
            /** Continued Page Header */
            continued_page_header?: string | null;
            /** Versions */
            versions?: string[];
            /** Version Questions */
            version_questions?: {
                [key: string]: components["schemas"]["QAQuestion"][];
            };
            /**
             * Questions Start Page
             * @default 1
             */
            questions_start_page: number;
        };
        /** QAPreview */
        QAPreview: {
            document: components["schemas"]["QADocument"];
            /** Warnings */
            warnings: string[];
        };
        /** QAQuestion */
        QAQuestion: {
            /** Id */
            id: string;
            /** Prompt */
            prompt: string;
            /** Points */
            points: number;
            /** Answers */
            answers?: string[];
            /** Choices */
            choices?: components["schemas"]["QAChoice"][];
            /** Correct Choice Label */
            correct_choice_label?: string | null;
            /**
             * Correct Choice Labels
             * @default []
             */
            correct_choice_labels: string[];
            /** Rubric */
            rubric?: components["schemas"]["QARubricItem"][];
            /** Image Markdown */
            image_markdown?: string | null;
            /** Subquestions */
            subquestions?: components["schemas"]["QASubquestion"][];
            /**
             * Outcomes
             * @default []
             */
            outcomes: string[];
            /** Source Line */
            source_line?: number | null;
        };
        /** QARubricItem */
        QARubricItem: {
            /** Label */
            label: string;
            /** Points Delta */
            points_delta?: number | null;
            /** Notes */
            notes?: string[];
            /** Subitems */
            subitems?: components["schemas"]["QARubricItem"][];
            /** Default Comment */
            default_comment?: string | null;
        };
        /** QASubquestion */
        QASubquestion: {
            /** Id */
            id: string;
            /** Prompt */
            prompt: string;
            /** Points */
            points: number;
            /** Label */
            label?: string | null;
            /** Answers */
            answers?: string[];
            /** Choices */
            choices?: components["schemas"]["QAChoice"][];
            /** Correct Choice Label */
            correct_choice_label?: string | null;
            /**
             * Correct Choice Labels
             * @default []
             */
            correct_choice_labels: string[];
            /** Rubric */
            rubric?: components["schemas"]["QARubricItem"][];
            /** Image Markdown */
            image_markdown?: string | null;
            /**
             * Outcomes
             * @default []
             */
            outcomes: string[];
            /** Source Line */
            source_line?: number | null;
        };
        /** Question */
        Question: {
            /** Id */
            id: string;
            /** Prompt */
            prompt: string;
            /** Possible Points */
            possible_points: number;
            /**
             * Question Type
             * @enum {string}
             */
            question_type: "free_response" | "multiple_choice" | "multiple_answer" | "numeric";
            /** Expected Answers */
            expected_answers: string[];
            /** Rubric */
            rubric: components["schemas"]["RubricOption"][];
        };
        /** RubricOption */
        RubricOption: {
            /** Label */
            label: string;
            /** Points Delta */
            points_delta: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "correct" | "partial" | "incorrect" | "bonus";
            /**
             * Selection Mode
             * @enum {string}
             */
            selection_mode: "exclusive" | "checklist";
            /** Default Comment */
            default_comment: string | null;
        };
        /** Submission */
        Submission: {
            /** Id */
            id: number;
            /** Version */
            version: string | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_assignments_assignments_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Assignment"][];
                };
            };
        };
    };
    create_assignment_assignments_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssignmentInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Assignment"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_assignment_assignments_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssignmentInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QAPreview"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_assignment_pdf_assignments_preview_pdf_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssignmentPDFInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/pdf": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_assignment_source_assignments__assignment_id__source_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssignmentInput"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_assignment_assignments__assignment_id__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssignmentUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Assignment"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_assignment_assignments__assignment_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_assignment_qa_assignments__assignment_id__qa_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QAPreview"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_assignment_pdf_assignments__assignment_id__pdf_get: {
        parameters: {
            query?: {
                version?: string | null;
            };
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/pdf": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_submissions_assignments__assignment_id__submissions_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Submission"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_questions_assignments__assignment_id__questions_get: {
        parameters: {
            query?: {
                submission_id?: number;
            };
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Question"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_answer_assignments__assignment_id__submissions__submission_id__answers__question_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Answer"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_answer_image_assignments__assignment_id__submissions__submission_id__answers__question_id__image_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
