class OpenGraderDomainError(Exception):
    pass


class CourseAlreadyExistsError(OpenGraderDomainError):
    pass


class CourseNotFoundError(OpenGraderDomainError):
    pass


class AssignmentNotFoundError(OpenGraderDomainError):
    pass


class AssignmentTemplateNotFoundError(OpenGraderDomainError):
    pass


class InvalidAssignmentTypeError(OpenGraderDomainError):
    pass


class InvalidPointsError(OpenGraderDomainError):
    pass


class StudentNotEnrolledError(OpenGraderDomainError):
    pass


class GradeConflictError(OpenGraderDomainError):
    pass
