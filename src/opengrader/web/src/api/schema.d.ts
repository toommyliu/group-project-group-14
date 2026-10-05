// Generated from FastAPI. Run pnpm generate:api to update.
export interface paths {
    "/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Auth Status */
        get: operations["auth_status_auth_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/view-as": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** View As */
        post: operations["view_as_auth_view_as_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logout_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/people": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** People */
        get: operations["people_api_people_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/people/graders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Grader */
        post: operations["add_grader_api_people_graders_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/people/graders/remove": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remove Grader */
        post: operations["remove_grader_api_people_graders_remove_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Workspace */
        get: operations["get_workspace_api_workspace_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/folder-session/sync": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sync Folder Session */
        post: operations["sync_folder_session_api_folder_session_sync_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/folder-session/convert": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Convert Folder Session */
        post: operations["convert_folder_session_api_folder_session_convert_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Assignments */
        get: operations["assignments_api_assignments_get"];
        put?: never;
        /** Create Assignment */
        post: operations["create_assignment_api_assignments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignment-summaries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Assignment Summaries */
        get: operations["assignment_summaries_api_assignment_summaries_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Submissions */
        post: operations["import_submissions_api_assignments__assignment_id__imports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drafts/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview */
        post: operations["preview_api_drafts_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drafts/edit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Edit Preview */
        post: operations["edit_preview_api_drafts_edit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drafts/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Pdf */
        post: operations["preview_pdf_api_drafts_pdf_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Assignment */
        put: operations["update_assignment_api_assignments__assignment_id__put"];
        post?: never;
        /** Delete Assignment */
        delete: operations["delete_assignment_api_assignments__assignment_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/source": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Source */
        get: operations["source_api_assignments__assignment_id__source_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/qa": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Qa */
        get: operations["qa_api_assignments__assignment_id__qa_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Pdf */
        get: operations["pdf_api_assignments__assignment_id__pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Submissions */
        get: operations["submissions_api_assignments__assignment_id__submissions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/statistics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Statistics */
        get: operations["statistics_api_assignments__assignment_id__statistics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions/{submission_id}/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Questions */
        get: operations["questions_api_assignments__assignment_id__submissions__submission_id__questions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions/{submission_id}/answers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Answers */
        get: operations["answers_api_assignments__assignment_id__submissions__submission_id__answers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions/{submission_id}/document": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Document */
        get: operations["document_api_assignments__assignment_id__submissions__submission_id__document_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Answer */
        get: operations["answer_api_assignments__assignment_id__submissions__submission_id__answers__question_id__get"];
        /** Save */
        put: operations["save_api_assignments__assignment_id__submissions__submission_id__answers__question_id__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assets/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Asset */
        get: operations["asset_api_assets__name__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Status */
        get: operations["job_status_api_jobs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/submissions/{submission_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Feedback */
        get: operations["feedback_api_assignments__assignment_id__submissions__submission_id__feedback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{assignment_id}/{submission_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry */
        post: operations["retry_api_jobs__assignment_id___submission_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Events */
        get: operations["events_api_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Scans */
        get: operations["list_scans_api_assignments__assignment_id__scans_get"];
        put?: never;
        /** Upload Scan */
        post: operations["upload_scan_api_assignments__assignment_id__scans_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/analyze": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Analyze Scan */
        post: operations["analyze_scan_api_assignments__assignment_id__scans__batch_id__analyze_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/groups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Review Scan */
        put: operations["review_scan_api_assignments__assignment_id__scans__batch_id__groups_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/process": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Process Scan */
        post: operations["process_scan_api_assignments__assignment_id__scans__batch_id__process_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Scan Pdf */
        get: operations["scan_pdf_api_assignments__assignment_id__scans__batch_id__pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/remove": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remove Scan */
        post: operations["remove_scan_api_assignments__assignment_id__scans__batch_id__remove_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}": {
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
        delete: operations["delete_scan_api_assignments__assignment_id__scans__batch_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/restore": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Restore Scan */
        post: operations["restore_scan_api_assignments__assignment_id__scans__batch_id__restore_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/assignments/{assignment_id}/scans/{batch_id}/pages/{page_number}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Scan Page */
        get: operations["scan_page_api_assignments__assignment_id__scans__batch_id__pages__page_number__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/server": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Info */
        get: operations["info_api_server_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspaces": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Workspaces */
        get: operations["list_workspaces_api_workspaces_get"];
        put?: never;
        /** Create Workspace */
        post: operations["create_workspace_api_workspaces_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspaces/{workspace_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Workspace */
        put: operations["update_workspace_api_workspaces__workspace_id__put"];
        post?: never;
        /** Remove Workspace */
        delete: operations["remove_workspace_api_workspaces__workspace_id__delete"];
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
            /** Workspace Id */
            workspace_id: string;
            /** Context Revision */
            context_revision: string;
            /** Revision */
            revision: string;
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
            /**
             * Score Mode
             * @enum {string}
             */
            score_mode: "rubric" | "manual";
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
        /** AssignmentQuestionStatistics */
        AssignmentQuestionStatistics: {
            /** Prompt */
            prompt: string;
            /** Parent Id */
            parent_id: string | null;
            /** Parent Prompt */
            parent_prompt: string | null;
            /** Submission Id */
            submission_id: number | null;
            scores: components["schemas"]["QuestionStatistics"];
        };
        /** AssignmentSummary */
        AssignmentSummary: {
            /** Id */
            id: string;
            /** Title */
            title: string;
            /** Question Count */
            question_count: number | null;
            /** Possible Points */
            possible_points: number | null;
            /** Version Count */
            version_count: number;
            /** Submission Count */
            submission_count: number;
            /** Graded Submission Count */
            graded_submission_count: number;
            /** Response Count */
            response_count: number;
            /** Graded Response Count */
            graded_response_count: number;
            /** Average Percent */
            average_percent: number | null;
        };
        /** AssignmentUpdate */
        AssignmentUpdate: {
            /** Qa Markdown */
            qa_markdown: string;
            /** Expected Qa Markdown */
            expected_qa_markdown: string;
        };
        /** AssignmentVersionStatistics */
        AssignmentVersionStatistics: {
            /** Version */
            version: string | null;
            scores: components["schemas"]["QuestionStatistics"];
            /** Partial Count */
            partial_count: number;
            /** Ungraded Count */
            ungraded_count: number;
            /** Questions */
            questions: components["schemas"]["AssignmentQuestionStatistics"][];
        };
        /** AuthStatus */
        AuthStatus: {
            /** Enabled */
            enabled: boolean;
            user?: components["schemas"]["AuthUser"] | null;
            /** Role */
            role?: ("instructor" | "grader") | null;
            /**
             * Viewing As Grader
             * @default false
             */
            viewing_as_grader: boolean;
        };
        /** AuthUser */
        AuthUser: {
            /** Id */
            id: string;
            /** Email */
            email: string;
            /** Name */
            name: string;
        };
        /** CourseDetails */
        CourseDetails: {
            /** Number */
            number: string;
            /** Name */
            name?: string | null;
            /** Term */
            term?: ("winter" | "spring" | "summer" | "fall") | null;
            /** Year */
            year?: number | null;
        };
        /** DocumentEdit */
        DocumentEdit: {
            /** Title */
            title: string;
            /** Instructions */
            instructions: string;
            /** Versions */
            versions?: components["schemas"]["VersionEdit"][] | null;
            /** Question Orders */
            question_orders?: components["schemas"]["QuestionOrder"][] | null;
        };
        /** FeedbackJob */
        FeedbackJob: {
            /** Assignment Id */
            assignment_id: string;
            /** Submission Id */
            submission_id: number;
            /** Generation */
            generation: number;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "running" | "ready" | "failed";
            /** Error */
            error: string;
            /** Artifact */
            artifact: string | null;
        };
        /** FolderSessionInfo */
        FolderSessionInfo: {
            /** Assignment Id */
            assignment_id: string;
            /** Assignment Title */
            assignment_title: string;
            /** Folder */
            folder: string;
        };
        /** FolderSyncResult */
        FolderSyncResult: {
            /**
             * Imported
             * @default 0
             */
            imported: number;
            /**
             * Updated
             * @default 0
             */
            updated: number;
            /**
             * Written
             * @default 0
             */
            written: number;
            /**
             * Failed
             * @default 0
             */
            failed: number;
            /**
             * Problems
             * @default []
             */
            problems: string[];
        };
        /**
         * FolderWrite
         * @description What happened to the class folder file when a folder session saved a grade.
         *
         *     The grade itself is committed either way; a failed file write only means
         *     the `.submission.md` still has the older value until the next sync.
         */
        FolderWrite: {
            /**
             * Status
             * @enum {string}
             */
            status: "written" | "unchanged" | "failed";
            /** Path */
            path?: string | null;
            /** Message */
            message?: string | null;
        };
        /** GradeEdit */
        GradeEdit: {
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /** Expected Context Revision */
            expected_context_revision: string;
            /** Expected Revision */
            expected_revision: string;
            /** Rubric Labels */
            rubric_labels: string[];
            /** Analysis */
            analysis: string;
            /**
             * Score Mode
             * @default rubric
             * @enum {string}
             */
            score_mode: "rubric" | "manual";
            /** Points Awarded */
            points_awarded?: number | null;
        };
        /** GradeSaveResult */
        GradeSaveResult: {
            answer: components["schemas"]["Answer"];
            folder?: components["schemas"]["FolderWrite"] | null;
        };
        /** Grader */
        Grader: {
            /** Email */
            email: string;
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
        /** People */
        People: {
            /** Domain */
            domain: string;
            /** Instructors */
            instructors: string[];
            /** Graders */
            graders: string[];
        };
        /** PipelineOutput */
        PipelineOutput: {
            /** Qa Markdown */
            qa_markdown: string;
            /** Submissions Dir */
            submissions_dir: string;
        };
        /** PipelinePublication */
        PipelinePublication: {
            assignment: components["schemas"]["Assignment"];
            /** Submission Count */
            submission_count: number;
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
        /** QAErrorLocation */
        QAErrorLocation: {
            /** Line */
            line?: number | null;
            /**
             * Version
             * @default
             */
            version: string;
            /** Question Path */
            question_path?: number[];
            /** Rubric Path */
            rubric_path?: number[] | null;
        };
        /** QALineNote */
        QALineNote: {
            /** Line */
            line: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "continuation" | "ignored";
            /** Message */
            message: string;
        };
        /** QAPreview */
        QAPreview: {
            document: components["schemas"]["QADocument"];
            /** Warnings */
            warnings: string[];
            /** Notes */
            notes: components["schemas"]["QALineNote"][];
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
            rubric?: components["schemas"]["QARubricItem-Output"][];
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
        "QARubricItem-Input": {
            /** Label */
            label: string;
            /** Points Delta */
            points_delta?: number | null;
            /** Notes */
            notes?: string[];
            /** Subitems */
            subitems?: components["schemas"]["QARubricItem-Input"][];
            /** Default Comment */
            default_comment?: string | null;
            /** Source Line */
            source_line?: number | null;
        };
        /** QARubricItem */
        "QARubricItem-Output": {
            /** Label */
            label: string;
            /** Points Delta */
            points_delta?: number | null;
            /** Notes */
            notes?: string[];
            /** Subitems */
            subitems?: components["schemas"]["QARubricItem-Output"][];
            /** Default Comment */
            default_comment?: string | null;
            /** Source Line */
            source_line?: number | null;
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
            rubric?: components["schemas"]["QARubricItem-Output"][];
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
        /** QuestionEdit */
        QuestionEdit: {
            /** Source Line */
            source_line: number;
            /** Prompt */
            prompt: string;
            /** Points */
            points: number;
            /** Answers */
            answers: string[];
            /** Rubric */
            rubric: components["schemas"]["QARubricItem-Input"][];
            /** Choices */
            choices?: components["schemas"]["QAChoice"][] | null;
            /** Correct Choice Labels */
            correct_choice_labels?: string[] | null;
            /** Version */
            version?: string | null;
            /**
             * Remove
             * @default false
             */
            remove: boolean;
        };
        /** QuestionOrder */
        QuestionOrder: {
            /** Version */
            version: string;
            /** Source Lines */
            source_lines: number[];
            /** Parent Source Line */
            parent_source_line?: number | null;
        };
        /** QuestionStatistics */
        QuestionStatistics: {
            /** Question Id */
            question_id: string;
            /** Possible Points */
            possible_points: number;
            /** Total Submissions */
            total_submissions: number;
            /** Graded Count */
            graded_count: number;
            /** Ungraded Count */
            ungraded_count: number;
            /** Average */
            average: number | null;
            /** Median */
            median: number | null;
            /** Std Dev */
            std_dev: number | null;
            /** Minimum */
            minimum: number | null;
            /** Maximum */
            maximum: number | null;
            /** Q1 */
            q1: number | null;
            /** Q3 */
            q3: number | null;
            /** Average Percent */
            average_percent: number | null;
            /** Full Credit Count */
            full_credit_count: number;
            /** Zero Score Count */
            zero_score_count: number;
            /** Buckets */
            buckets: components["schemas"]["ScoreBucket"][];
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
            /** Scan Warnings */
            scan_warnings: string[];
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
        /** ScoreBucket */
        ScoreBucket: {
            /** Label */
            label: string;
            /** Count */
            count: number;
        };
        /** Submission */
        Submission: {
            /**
             * Student Name
             * @default
             */
            student_name: string;
            /**
             * Student Id
             * @default
             */
            student_id: string;
            /** Id */
            id: number;
            /** Version */
            version: string | null;
            /** Question Scores */
            question_scores: {
                [key: string]: number | null;
            };
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
        /** VersionEdit */
        VersionEdit: {
            /** Key */
            key: string;
            /** Original Label */
            original_label: string | null;
            /** Label */
            label: string;
        };
        /** ViewAs */
        ViewAs: {
            /**
             * Role
             * @enum {string}
             */
            role: "instructor" | "grader";
        };
        /** VisualEditInput */
        VisualEditInput: {
            /** Qa Markdown */
            qa_markdown: string;
            /** Edits */
            edits: components["schemas"]["QuestionEdit"][];
            metadata?: components["schemas"]["DocumentEdit"] | null;
        };
        /** VisualEditResult */
        VisualEditResult: {
            /** Qa Markdown */
            qa_markdown: string;
            preview: components["schemas"]["QAPreview"] | null;
            /** Validation Error */
            validation_error: string | null;
            validation_location?: components["schemas"]["QAErrorLocation"] | null;
        };
        /** WorkspaceInfo */
        WorkspaceInfo: {
            /** Id */
            id: string;
            course?: components["schemas"]["CourseDetails"] | null;
            /** Actor */
            actor: string;
            /** Event Cursor */
            event_cursor: number;
            folder_session?: components["schemas"]["FolderSessionInfo"] | null;
        };
        /** CourseForm */
        CourseForm: {
            /** Number */
            number: string;
            /** Name */
            name: string;
            /** Term */
            term?: ("winter" | "spring" | "summer" | "fall") | null;
            /** Year */
            year?: number | null;
        };
        /** ServerInfo */
        ServerInfo: {
            /** Id */
            id: string;
        };
        /** WorkspaceSummary */
        WorkspaceSummary: {
            /** Number */
            number: string;
            /** Name */
            name?: string | null;
            /** Term */
            term?: ("winter" | "spring" | "summer" | "fall") | null;
            /** Year */
            year?: number | null;
            /** Id */
            id: string;
            /** Url */
            url: string;
            /** Last Opened */
            last_opened: string | null;
            /** Available */
            available: boolean;
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
    auth_status_auth_me_get: {
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
                    "application/json": components["schemas"]["AuthStatus"];
                };
            };
        };
    };
    view_as_auth_view_as_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ViewAs"];
            };
        };
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
    logout_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
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
        };
    };
    people_api_people_get: {
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
                    "application/json": components["schemas"]["People"];
                };
            };
        };
    };
    add_grader_api_people_graders_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Grader"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["People"];
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
    remove_grader_api_people_graders_remove_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Grader"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["People"];
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
    get_workspace_api_workspace_get: {
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
                    "application/json": components["schemas"]["WorkspaceInfo"];
                };
            };
        };
    };
    sync_folder_session_api_folder_session_sync_post: {
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
                    "application/json": components["schemas"]["FolderSyncResult"];
                };
            };
        };
    };
    convert_folder_session_api_folder_session_convert_post: {
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
                    "application/json": components["schemas"]["WorkspaceInfo"];
                };
            };
        };
    };
    assignments_api_assignments_get: {
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
    create_assignment_api_assignments_post: {
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
    assignment_summaries_api_assignment_summaries_get: {
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
                    "application/json": components["schemas"]["AssignmentSummary"][];
                };
            };
        };
    };
    import_submissions_api_assignments__assignment_id__imports_post: {
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
                "application/json": components["schemas"]["PipelineOutput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PipelinePublication"];
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
    preview_api_drafts_preview_post: {
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
    edit_preview_api_drafts_edit_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VisualEditInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VisualEditResult"];
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
    preview_pdf_api_drafts_pdf_post: {
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
    update_assignment_api_assignments__assignment_id__put: {
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
    delete_assignment_api_assignments__assignment_id__delete: {
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
    source_api_assignments__assignment_id__source_get: {
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
    qa_api_assignments__assignment_id__qa_get: {
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
    pdf_api_assignments__assignment_id__pdf_get: {
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
    submissions_api_assignments__assignment_id__submissions_get: {
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
    statistics_api_assignments__assignment_id__statistics_get: {
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
                    "application/json": components["schemas"]["AssignmentVersionStatistics"][];
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
    questions_api_assignments__assignment_id__submissions__submission_id__questions_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
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
    answers_api_assignments__assignment_id__submissions__submission_id__answers_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
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
                    "application/json": components["schemas"]["Answer"][];
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
    document_api_assignments__assignment_id__submissions__submission_id__document_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
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
    answer_api_assignments__assignment_id__submissions__submission_id__answers__question_id__get: {
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
    save_api_assignments__assignment_id__submissions__submission_id__answers__question_id__put: {
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
        requestBody: {
            content: {
                "application/json": components["schemas"]["GradeEdit"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GradeSaveResult"];
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
    asset_api_assets__name__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
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
    job_status_api_jobs_get: {
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
                    "application/json": components["schemas"]["FeedbackJob"][];
                };
            };
        };
    };
    feedback_api_assignments__assignment_id__submissions__submission_id__feedback_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
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
    retry_api_jobs__assignment_id___submission_id__retry_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assignment_id: string;
                submission_id: number;
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
    events_api_events_get: {
        parameters: {
            query?: {
                after?: number;
            };
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
    list_scans_api_assignments__assignment_id__scans_get: {
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
    upload_scan_api_assignments__assignment_id__scans_post: {
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
    analyze_scan_api_assignments__assignment_id__scans__batch_id__analyze_post: {
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
    review_scan_api_assignments__assignment_id__scans__batch_id__groups_put: {
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
    process_scan_api_assignments__assignment_id__scans__batch_id__process_post: {
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
    scan_pdf_api_assignments__assignment_id__scans__batch_id__pdf_get: {
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
    remove_scan_api_assignments__assignment_id__scans__batch_id__remove_post: {
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
    delete_scan_api_assignments__assignment_id__scans__batch_id__delete: {
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
    restore_scan_api_assignments__assignment_id__scans__batch_id__restore_post: {
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
    scan_page_api_assignments__assignment_id__scans__batch_id__pages__page_number__get: {
        parameters: {
            query?: {
                size?: "small" | "large";
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
    info_api_server_get: {
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
                    "application/json": components["schemas"]["ServerInfo"];
                };
            };
        };
    };
    list_workspaces_api_workspaces_get: {
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
                    "application/json": components["schemas"]["WorkspaceSummary"][];
                };
            };
        };
    };
    create_workspace_api_workspaces_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CourseForm"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceSummary"];
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
    update_workspace_api_workspaces__workspace_id__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                workspace_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CourseForm"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceSummary"];
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
    remove_workspace_api_workspaces__workspace_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                workspace_id: string;
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
}
