class LocalGraderDomainError(Exception):
    pass

class CourseAlreadyExistsError(LocalGraderDomainError):
    pass

class CourseNotFoundError(LocalGraderDomainError):
    pass

class AssessmentNotFoundError(LocalGraderDomainError):
    pass

class InvalidAssessmentTypeError(LocalGraderDomainError):
    pass

class InvalidPointsError(LocalGraderDomainError):
    pass

class StudentNotEnrolledError(LocalGraderDomainError):
    pass

class GradeConflictError(LocalGraderDomainError):
    pass
