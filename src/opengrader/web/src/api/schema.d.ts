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
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
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
