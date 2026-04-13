from opengrader.core.pdf.templates.constants import QuestionType


REQUIRED_SCHEMA_KEYS = {"schema_version", "unit_id", "pages"}
VALID_FIELD_TYPES = {
    "text",
    "name",
    "sid",
    "section",
    "custom",
    "version_marker",
}
VALID_RESPONSE_TYPE_FIELDS = {q.value for q in QuestionType}

# TODO: this might be useless if we disregard backward compatibility
SCHEMA_VERSION = "1.0"
