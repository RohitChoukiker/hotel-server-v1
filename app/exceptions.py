"""Domain exceptions and stable public error codes."""

from typing import Any


class DomainError(Exception):
    """Base class for errors safe to map at the HTTP boundary."""

    code = "DOMAIN_ERROR"
    message = "A domain error occurred"
    status_code = 400

    def __init__(self, message: str | None = None, details: Any | None = None) -> None:
        super().__init__(message or self.message)
        self.public_message = message or self.message
        self.details = details


class NotFoundError(DomainError):
    """Base missing resource error."""

    code = "NOT_FOUND"
    message = "Resource not found"
    status_code = 404


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"
    message = "User not found"


class HotelNotFoundError(NotFoundError):
    code = "HOTEL_NOT_FOUND"
    message = "Hotel not found"


class ReviewNotFoundError(NotFoundError):
    code = "REVIEW_NOT_FOUND"
    message = "Review not found"


class TripNotFoundError(NotFoundError):
    code = "TRIP_NOT_FOUND"
    message = "Trip not found"


class PreferenceNotFoundError(NotFoundError):
    code = "PREFERENCE_NOT_FOUND"
    message = "Preference not found"


class RecommendationRunNotFoundError(NotFoundError):
    code = "RECOMMENDATION_RUN_NOT_FOUND"
    message = "Recommendation run not found"


class OnboardingSessionNotFoundError(NotFoundError):
    code = "ONBOARDING_SESSION_NOT_FOUND"
    message = "Onboarding session not found"


class InvalidCredentialsError(DomainError):
    code = "INVALID_CREDENTIALS"
    message = "Invalid email or password"
    status_code = 401


class InvalidTokenError(DomainError):
    code = "INVALID_TOKEN"
    message = "Token is invalid or expired"
    status_code = 401


class RefreshTokenReuseError(DomainError):
    code = "REFRESH_TOKEN_REUSE"
    message = "Refresh token reuse detected; token family revoked"
    status_code = 401


class EmailAlreadyExistsError(DomainError):
    code = "EMAIL_ALREADY_EXISTS"
    message = "An account with this email already exists"
    status_code = 409


class PhoneAlreadyExistsError(DomainError):
    code = "PHONE_ALREADY_EXISTS"
    message = "An account with this phone number already exists"
    status_code = 409


class UnauthorizedError(DomainError):
    code = "UNAUTHORIZED"
    message = "Authentication required"
    status_code = 401


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action"
    status_code = 403


class OwnershipError(ForbiddenError):
    code = "OWNERSHIP_REQUIRED"
    message = "You do not own this resource"


class InvalidDestinationError(DomainError):
    code = "INVALID_DESTINATION"
    message = "Trip destination is invalid"
    status_code = 422


class UnsupportedSourceError(DomainError):
    code = "UNSUPPORTED_SOURCE"
    message = "Data source is not supported"
    status_code = 422


class ImportValidationError(DomainError):
    code = "IMPORT_VALIDATION_ERROR"
    message = "Import file or row is invalid"
    status_code = 422


class DuplicateSourceEntityError(DomainError):
    code = "DUPLICATE_SOURCE_ENTITY"
    message = "Source entity already exists"
    status_code = 409


class ScoringError(DomainError):
    code = "SCORING_ERROR"
    message = "Unable to calculate scores"
    status_code = 422


class InsufficientAttributeDataError(ScoringError):
    code = "INSUFFICIENT_ATTRIBUTE_DATA"
    message = "There is not enough attribute evidence to score this hotel"


class OnboardingNotCompletedError(DomainError):
    code = "ONBOARDING_NOT_COMPLETED"
    message = "Complete onboarding before requesting recommendations"
    status_code = 409


class RateLimitExceededError(DomainError):
    code = "RATE_LIMIT_EXCEEDED"
    message = "Too many requests"
    status_code = 429


class DependencyUnavailableError(DomainError):
    code = "DEPENDENCY_UNAVAILABLE"
    message = "A required dependency is unavailable"
    status_code = 503


class SourceRateLimitError(DependencyUnavailableError):
    """Upstream source rate limit carrying a bounded retry delay."""

    code = "SOURCE_RATE_LIMITED"
    message = "The upstream source rate limit was reached"

    def __init__(self, retry_after_seconds: int = 60) -> None:
        self.retry_after_seconds = max(1, min(retry_after_seconds, 3600))
        super().__init__(details={"retry_after_seconds": self.retry_after_seconds})
