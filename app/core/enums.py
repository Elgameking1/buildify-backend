"""Shared enumerations.

These live outside the module packages so that models, schemas and services can
all import them without creating circular imports between modules.
"""

from enum import StrEnum


class UserRole(StrEnum):
    CLIENT = "CLIENT"
    VENDOR = "VENDOR"
    WORKER = "WORKER"
    ADMIN = "ADMIN"


class ProductUnit(StrEnum):
    """Construction materials are not sold 'each'.

    A closed set of units is what makes price comparison across vendors
    meaningful - the core problem named in the proposal.
    """

    BAG = "BAG"
    TON = "TON"
    PIECE = "PIECE"
    METRE = "METRE"
    LITRE = "LITRE"
    BUNDLE = "BUNDLE"


class ProductStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    ARCHIVED = "ARCHIVED"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"


class VendorItemStatus(StrEnum):
    """Per-vendor fulfilment state of a single line in a multi-vendor order."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    READY = "READY"
    CANCELLED = "CANCELLED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"


class SkillProficiency(StrEnum):
    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    EXPERT = "EXPERT"


class MediaPurpose(StrEnum):
    PRODUCT = "product"
    PORTFOLIO = "portfolio"
    LOGO = "logo"


class NotificationType(StrEnum):
    JOB_REQUEST_RECEIVED = "JOB_REQUEST_RECEIVED"
    JOB_ACCEPTED = "JOB_ACCEPTED"
    JOB_DECLINED = "JOB_DECLINED"
    JOB_IN_PROGRESS = "JOB_IN_PROGRESS"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_CANCELLED = "JOB_CANCELLED"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_ITEM_UPDATED = "ORDER_ITEM_UPDATED"
    REVIEW_RECEIVED = "REVIEW_RECEIVED"
