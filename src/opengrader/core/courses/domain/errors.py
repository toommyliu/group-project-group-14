class OpenGraderDomainError(Exception):
    pass

class CourseAlreadyExistsError(OpenGraderDomainError):
    pass

class CourseNotFoundError(OpenGraderDomainError):
    pass

class AssessmentNotFoundError(OpenGraderDomainError):
    pass

class InvalidAssessmentTypeError(OpenGraderDomainError):
    pass

class InvalidPointsError(OpenGraderDomainError):
    pass

class StudentNotEnrolledError(OpenGraderDomainError):
    pass

class GradeConflictError(OpenGraderDomainError):
    pass
