"""Shared domain enumerations."""

from enum import StrEnum


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class UserRole(StrEnum):
    """Application roles ordered from least to most privileged."""

    USER = "USER"
    DATA_OPERATOR = "DATA_OPERATOR"
    ADMIN = "ADMIN"


class UserStatus(StrEnum):
    """User account states."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class RegionType(StrEnum):
    """Supported administrative region types."""

    STATE = "STATE"
    UNION_TERRITORY = "UNION_TERRITORY"
    PROVINCE = "PROVINCE"
    REGION = "REGION"
    EMIRATE = "EMIRATE"


class Sentiment(StrEnum):
    """Review-attribute sentiment."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class PreferenceSource(StrEnum):
    """Preference provenance and precedence."""

    ONBOARDING_AI = "ONBOARDING_AI"
    USER_MANUAL = "USER_MANUAL"


class TripStatus(StrEnum):
    """Trip lifecycle states."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TripPurpose(StrEnum):
    """Supported high-level trip purposes."""

    LEISURE = "LEISURE"
    BUSINESS = "BUSINESS"
    HONEYMOON = "HONEYMOON"
    FAMILY = "FAMILY"
    SOLO = "SOLO"
    FRIENDS = "FRIENDS"


class JobStatus(StrEnum):
    """Background job states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ImportType(StrEnum):
    """Import data types."""

    HOTELS = "HOTELS"
    REVIEWS = "REVIEWS"


class ScopeType(StrEnum):
    """Normalization population scopes."""

    GLOBAL = "GLOBAL"
    COUNTRY = "COUNTRY"
    REGION = "REGION"
    CITY = "CITY"
    SEARCH_SET = "SEARCH_SET"


class ChatIntent(StrEnum):
    """Supported assistant intents."""

    SEARCH_HOTELS = "SEARCH_HOTELS"
    RECOMMEND_HOTELS = "RECOMMEND_HOTELS"
    COMPARE_HOTELS = "COMPARE_HOTELS"
    SHOW_REVIEWS = "SHOW_REVIEWS"
    SHOW_NEGATIVE_REVIEWS = "SHOW_NEGATIVE_REVIEWS"
    SHOW_IMAGES = "SHOW_IMAGES"
    SHOW_ATTRIBUTES = "SHOW_ATTRIBUTES"
    EXPLAIN_RECOMMENDATION = "EXPLAIN_RECOMMENDATION"
    UPDATE_TRIP_PREFERENCE = "UPDATE_TRIP_PREFERENCE"
    CREATE_TRIP = "CREATE_TRIP"
    GENERAL_HOTEL_QUESTION = "GENERAL_HOTEL_QUESTION"

