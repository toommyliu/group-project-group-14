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
    "/assignments/{assignment_id}/scans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Scans */
        get: operations["list_scans_assignments__assignment_id__scans_get"];
        put?: never;
        /** Upload Scan */
        post: operations["upload_scan_assignments__assignment_id__scans_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/analyze": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Analyze Scan */
        post: operations["analyze_scan_assignments__assignment_id__scans__batch_id__analyze_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Review Scan */
        put: operations["review_scan_assignments__assignment_id__scans__batch_id__review_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/process": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Process Scan */
        post: operations["process_scan_assignments__assignment_id__scans__batch_id__process_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Scan Pdf */
        get: operations["scan_pdf_assignments__assignment_id__scans__batch_id__pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/remove": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remove Scan */
        post: operations["remove_scan_assignments__assignment_id__scans__batch_id__remove_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Scan */
        delete: operations["delete_scan_assignments__assignment_id__scans__batch_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/restore": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Restore Scan */
        post: operations["restore_scan_assignments__assignment_id__scans__batch_id__restore_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/assignments/{assignment_id}/scans/{batch_id}/pages/{page_number}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Scan Page */
        get: operations["scan_page_assignments__assignment_id__scans__batch_id__pages__page_number__get"];
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
        /** AnalyzeScan */
        AnalyzeScan: {
            /** Expected Revision */
            expected_revision: number;
            /**
             * Mode
             * @default automatic
             * @enum {string}
             */
            mode: "automatic" | "fixed" | "individual";
            /** Pages Per Submission */
            pages_per_submission?: number | null;
        };
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
        /** PageSelection */
        "PageSelection-Input": {
            /** Number */
            number: number;
            /**
             * Rotation
             * @default 0
             * @enum {integer}
             */
            rotation: 0 | 90 | 180 | 270;
        };
        /** PageSelection */
        "PageSelection-Output": {
            /** Number */
            number: number;
            /**
             * Rotation
             * @default 0
             * @enum {integer}
             */
            rotation: 0 | 90 | 180 | 270;
        };
        /** ProcessScan */
        ProcessScan: {
            /** Expected Revision */
            expected_revision: number;
            /**
             * Acknowledge Findings
             * @default false
             */
            acknowledge_findings: boolean;
            /**
             * Auto Grade
             * @default false
             */
            auto_grade: boolean;
            /** Assignment Version */
            assignment_version?: string | null;
            /** Group Versions */
            group_versions?: {
                [key: string]: string | null;
            };
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
        /** ReviewScan */
        ReviewScan: {
            /** Expected Revision */
            expected_revision: number;
            /** Groups */
            groups: components["schemas"]["PageSelection-Input"][][];
            /** Excluded Pages */
            excluded_pages?: number[];
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
        /** ScanBatch */
        ScanBatch: {
            /** Id */
            id: string;
            /** Filename */
            filename: string;
            /** Sha256 */
            sha256: string;
            /** Page Count */
            page_count: number;
            /** Created At */
            created_at: string;
            /**
             * Status
             * @default uploaded
             * @enum {string}
             */
            status: "uploaded" | "checking" | "review" | "queued" | "processing" | "ready" | "failed" | "removed";
            /**
             * Revision
             * @default 1
             */
            revision: number;
            /**
             * Mode
             * @default automatic
             * @enum {string}
             */
            mode: "automatic" | "fixed" | "individual";
            /** Pages Per Submission */
            pages_per_submission: number | null;
            /** Groups */
            groups: components["schemas"]["ScanGroup"][];
            /** Excluded Pages */
            excluded_pages: number[];
            /** Findings */
            findings: components["schemas"]["ScanFinding"][];
            /**
             * Message
             * @default Choose how to separate this scan.
             */
            message: string;
            /**
             * Started
             * @default false
             */
            started: boolean;
            /**
             * Auto Grade
             * @default false
             */
            auto_grade: boolean;
            /** Assignment Version */
            assignment_version: string | null;
        };
        /** ScanFinding */
        ScanFinding: {
            /** Page */
            page: number | null;
            /** Severity */
            severity: string;
            /** Detail */
            detail: string;
        };
        /** ScanGroup */
        ScanGroup: {
            /** Key */
            key: string;
            /** Pages */
            pages: components["schemas"]["PageSelection-Output"][];
            /** Submission Id */
            submission_id: number | null;
            /**
             * Error
             * @default
             */
            error: string;
            /** Assignment Version */
            assignment_version: string | null;
        };
        /** ScanList */
        ScanList: {
            /** Batches */
            batches: components["schemas"]["ScanBatch"][];
            /**
             * Max File Bytes
             * @default 262144000
             */
            max_file_bytes: number;
            /**
             * Max Pages
             * @default 3000
             */
            max_pages: number;
        };
        /** ScanRevision */
        ScanRevision: {
            /** Expected Revision */
            expected_revision: number;
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
    list_scans_assignments__assignment_id__scans_get: {
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
                    "application/json": components["schemas"]["ScanList"];
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
    upload_scan_assignments__assignment_id__scans_post: {
        parameters: {
            query: {
                filename: string;
            };
            header?: never;
            path: {
                assignment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/pdf": string;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    analyze_scan_assignments__assignment_id__scans__batch_id__analyze_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnalyzeScan"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    review_scan_assignments__assignment_id__scans__batch_id__review_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewScan"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    process_scan_assignments__assignment_id__scans__batch_id__process_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProcessScan"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    scan_pdf_assignments__assignment_id__scans__batch_id__pdf_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
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
    remove_scan_assignments__assignment_id__scans__batch_id__remove_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScanRevision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    delete_scan_assignments__assignment_id__scans__batch_id__delete: {
        parameters: {
            query: {
                expected_revision: number;
            };
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
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
    restore_scan_assignments__assignment_id__scans__batch_id__restore_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScanRevision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScanBatch"];
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
    scan_page_assignments__assignment_id__scans__batch_id__pages__page_number__get: {
        parameters: {
            query?: {
                large?: boolean;
            };
            header?: never;
            path: {
                assignment_id: string;
                batch_id: string;
                page_number: number;
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
